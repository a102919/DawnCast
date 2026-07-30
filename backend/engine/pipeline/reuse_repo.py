"""引擎專用參數化 SQL repo（全用 shared.db.pool.connection）。

夜間 pipeline 的寫入與重用查詢——投影訂單、episode upsert/更新、pipeline run
forensic 紀錄、重用決策、交付、push 通知批次認領、evergreen 兜底。只有 engine/
層呼叫（worker、reuse.py、evergreen.py、langgraph_pod、scripts/generate_one.py）；
跨層共用的訂單狀態轉移 / 交付查詢留在 shared/db/repo.py（app 也會呼叫）。

SQL 全參數化，禁字串拼接。重用查詢核心是單一 anti-join（見 reuse.py），
不在這層做特例分支——讓「過期」與「已交付」都收斂成同一條 WHERE。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row

from shared.db.pool import connection
from shared.models import ENTRY_MODE_TO_TOPIC_TYPE, Cue


async def project_order_to_request(order_id: str) -> dict[str, Any] | None:
    """把單筆 daily_order 投影成 topic_requests，回傳重用查詢用的 dict。

    取代舊版「project_orders_to_requests(當天) + list_requests_for_date(當天)」
    這組批次配對——隨時點餐下每筆訂單各自即時觸發，不再有「當天」這個聚合
    單位。冪等：先刪掉這個 order_id 舊的投影列再重投，order_reconcile 重放
    orchestrate 時安全。查無此 order 回 None（可能已被取消）。

    selected_topics + specific_request 併成 raw_topic；兩者皆空時標
    source='fallback'，並用 users.onboarding_big_topic 當題目來源。
    entry_mode → topic_type 用 Python 端 shared.models.ENTRY_MODE_TO_TOPIC_TYPE
    算好、當參數傳進 INSERT（不用 SQL CASE）：對映邏輯只有一份，跟 EntryMode /
    TopicType 型別定義放在一起讓型別檢查器強制窮盡，不會 SQL 與 Python 各寫
    一份、日後漂移。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.topic_requests where order_id = %s",
            (order_id,),
        )
        # array_to_string + nullif 把 selected_topics（jsonb 陣列）與 specific_request
        # 併成單一 raw_topic；兩者皆空→ NULL → source='fallback'、raw 用大主題。
        # 同樣的 concat 邏輯重複兩次（raw_topic 欄位、source 判定各算一次），
        # 重新算出來不額外加 CTE，保持單一 SELECT 一眼看懂。
        await cur.execute(
            """
            select
                o.id as order_id,
                o.user_id,
                o.order_date,
                o.entry_mode,
                o.length_tier,
                u.cefr_target as cefr,
                coalesce(
                    nullif(
                        trim(both ' ' from concat_ws(
                            ' ',
                            nullif((
                                select string_agg(value, ' ')
                                from jsonb_array_elements_text(o.selected_topics)
                            ), ''),
                            nullif(o.specific_request, '')
                        )),
                        ''
                    ),
                    u.onboarding_big_topic
                ) as raw_topic,
                case
                    when nullif(
                        trim(both ' ' from concat_ws(
                            ' ',
                            nullif((
                                select string_agg(value, ' ')
                                from jsonb_array_elements_text(o.selected_topics)
                            ), ''),
                            nullif(o.specific_request, '')
                        )),
                        ''
                    ) is null then 'fallback'
                    else 'specified'
                end as source
            from public.daily_orders o
            join public.users u on u.id = o.user_id
            where o.id = %s
            """,
            (order_id,),
        )
        row = await cur.fetchone()
        if row is None or row["raw_topic"] is None:
            return None
        topic_type = ENTRY_MODE_TO_TOPIC_TYPE[row["entry_mode"]]
        await cur.execute(
            """
            insert into public.topic_requests
                (user_id, request_date, raw_topic, source, topic_type, length_tier, order_id)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row["user_id"],
                row["order_date"],
                row["raw_topic"],
                row["source"],
                topic_type,
                row["length_tier"],
                order_id,
            ),
        )
    return {
        "user_id": str(row["user_id"]),
        "big_topic": row["raw_topic"],
        "topic_type": topic_type,
        "length_tier": row["length_tier"],
        "source": row["source"],
        "cefr": row["cefr"] or "B1",
    }


async def upsert_episode(
    *,
    idempotency_key: str,
    slug: str,
    title: str,
    topic: str,
    big_topic: str,
    angle: str,
    topic_type: str,
    cefr_level: str = "B1",
    title_zh: str | None = None,
    cluster_id: str | None = None,
    length_tier: str = "medium",
    format: str = "dialogue",
    grounded: bool = False,
    input_tokens: int = 0,
    output_tokens: int = 0,
    is_free: bool = True,
    sources: list[dict[str, Any]] | None = None,
    generation_started_at: datetime | None = None,
    gen_metrics: dict[str, Any] | None = None,
    research_metrics: dict[str, Any] | None = None,
    channel_id: str | None = None,
    episode_no: int | None = None,
) -> tuple[str, bool]:
    """建一列 episodes（媒體 key / cues 之後用 update_episode_keys 補）。

    回傳 (episode_id, already_rendered)：
      - 冪等鍵未衝突 → 新建列，already_rendered=False。
      - 衝突（同 key 已存在）→ 復用既有列，避免重投時重複建集與 R2 孤兒物件。
        already_rendered = 既有列是否已渲染完成（audio_r2_key 非空），
        讓上層跳過重渲染、只補交付。

    is_free：預設 True（公開，登入即可看）。呼叫端（upsert_episode_node）依
    topic_requests.source 是否為 'specified' 算出 False，做成該使用者專屬集。

    sources：可選，落 episodes.sources jsonb。預設 None → 寫入空 list。
    落原文 SourceSnippet（{id,title,url,text,published_at}），URL 安全過濾
    由 app 層 router 對外輸出時再做。

    generation_started_at / gen_metrics / research_metrics：上集生成的分階段耗時
    與研究過程摘要（見 langgraph_pod/metrics.py）。此時 render/upload 尚未跑完，
    gen_metrics 只到 write_script/judge 為止；完整版由 update_episode_keys 補寫。

    channel_id / episode_no：頻道機制專用，預設 None 保持向後相容（既有個人化
    生成路徑不屬於任何頻道，不傳這兩個參數也不會壞）。episode_no 是頻道內流水號
    （由呼叫端先呼叫 shared.db.channels.next_episode_no 算好再傳進來，這層只負責
    落庫，不重複計算）。兩者都刻意不進 idempotency_key——見 upsert_episode_node
    的說明，canonical_topic 已足以區分內容。
    """
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    gen_metrics_json = json.dumps(gen_metrics or {}, ensure_ascii=False)
    research_metrics_json = json.dumps(research_metrics or {}, ensure_ascii=False)
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.episodes
                (slug, title, title_zh, topic, cefr_level,
                 big_topic, angle, freshness_class, source_cluster_id,
                 idempotency_key, length_tier, format, grounded,
                 input_tokens, output_tokens, is_free, sources,
                 generation_started_at, gen_metrics, research_metrics,
                 channel_id, episode_no)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s)
            on conflict (idempotency_key) do nothing
            returning id
            """,
            (
                slug,
                title,
                title_zh,
                topic,
                cefr_level,
                big_topic,
                angle,
                _freshness_for(topic_type),
                cluster_id,
                idempotency_key,
                length_tier,
                format,
                grounded,
                input_tokens,
                output_tokens,
                is_free,
                sources_json,
                generation_started_at,
                gen_metrics_json,
                research_metrics_json,
                channel_id,
                episode_no,
            ),
        )
        row = await cur.fetchone()
        if row is not None:
            return str(row["id"]), False
        # 衝突：撈既有列，判斷是否已渲染完成
        await cur.execute(
            """
            select id, audio_r2_key
            from public.episodes
            where idempotency_key = %s
            """,
            (idempotency_key,),
        )
        existing = await cur.fetchone()
    if existing is None:
        raise RuntimeError("冪等鍵衝突但撈不到既有集")
    return str(existing["id"]), existing["audio_r2_key"] is not None


def _freshness_for(topic_type: str) -> str:
    """topic_type → freshness_class。news/product 是有時效的；其餘當常青。"""
    return "timely" if topic_type in ("news", "product") else "evergreen"


async def update_episode_keys(
    episode_id: str,
    *,
    audio_key: str | None = None,
    audio_keys: list[str] | None = None,
    srt_key: str | None = None,
    script_json: dict[str, Any],
    cues: list[Cue],
    extracted_facts: list[dict[str, Any]] | None = None,
    target_vocab: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    generation_finished_at: datetime | None = None,
    gen_metrics: dict[str, Any] | None = None,
) -> None:
    """渲染完成後回填媒體 key 與內容。script_json 內含 cues（前端播放頁吃這個）。

    audio_key / audio_keys：新方案用 audio_keys（list[str]，每行 mp3 一個 key）；
    舊 audio_key 欄位保留寫 audio_keys[0] 給向後相容（admin / 部分 router 仍會讀）。
    兩者皆 None 時不更新（測試 / escape hatch）。

    sources：可選；非 None 時覆寫 episodes.sources。傳 None 表示保留既有值
    （避免 update_episode_keys_node 在還沒拿到 retrieve_sources 結果時誤清空）。

    generation_finished_at / gen_metrics：render_episode + upload_artifacts 跑完後
    的完整 metrics（含 render/upload 兩個 stage），覆寫 upsert_episode 當時寫入的
    半成品版本。傳 None 表示保留既有值（測試 / 沒接 metrics 的呼叫端）。
    """
    payload = dict(script_json)
    payload["cues"] = [c.model_dump(by_alias=False) for c in cues]
    sources_json: str | None = (
        json.dumps(sources, ensure_ascii=False) if sources is not None else None
    )
    gen_metrics_json: str | None = (
        json.dumps(gen_metrics, ensure_ascii=False) if gen_metrics is not None else None
    )
    audio_keys_json = json.dumps(audio_keys, ensure_ascii=False) if audio_keys is not None else None
    # audio_r2_key 舊欄位：寫 audio_keys[0] 給向後相容（admin / 部分 router 仍讀）。
    # audio_keys 為空 list 或 None 時 audio_r2_key 也保持 None。
    legacy_audio_key: str | None
    if audio_keys:
        legacy_audio_key = audio_keys[0]
    elif audio_key is not None:
        legacy_audio_key = audio_key
    else:
        legacy_audio_key = None
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            update public.episodes
            set audio_r2_key = coalesce(%s, audio_r2_key),
                audio_r2_keys = coalesce(%s::jsonb, audio_r2_keys),
                srt_r2_key = %s,
                script_json = %s::jsonb,
                extracted_facts = %s::jsonb,
                target_vocab = %s::jsonb,
                sources = coalesce(%s::jsonb, sources),
                generation_finished_at = coalesce(%s, generation_finished_at),
                gen_metrics = coalesce(%s::jsonb, gen_metrics)
            where id = %s
            """,
            (
                legacy_audio_key,
                audio_keys_json,
                srt_key,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(extracted_facts, ensure_ascii=False)
                if extracted_facts is not None
                else None,
                json.dumps(target_vocab, ensure_ascii=False) if target_vocab is not None else None,
                sources_json,
                generation_finished_at,
                gen_metrics_json,
                episode_id,
            ),
        )


async def delete_episode_by_idem(idempotency_key: str) -> int:
    """compensation：用 idempotency_key 刪除半完成 episode row。

    只刪 audio_r2_key IS NULL 且 audio_r2_keys 為空 list 的列，避免 worker 重試
    在 race condition 下誤殺已正常完成的 row（含舊集只有 audio_r2_key 沒
    audio_r2_keys、backfill 已跑過的集）。回傳實際刪除列數。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            delete from public.episodes
            where idempotency_key = %s
              and audio_r2_key is null
              and audio_r2_keys = '[]'::jsonb
            """,
            (idempotency_key,),
        )
        return cur.rowcount


async def start_pipeline_run(idempotency_key: str, *, enqueued_at: datetime | None) -> str:
    """run_pod 開始時 INSERT 一筆 forensic row，即使後面 crash 也留得住紀錄。

    attempt 依同 idempotency_key 既有筆數遞增（同集因錯誤重投會有多筆）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.episode_pipeline_runs
                (idempotency_key, attempt, status, enqueued_at, started_at)
            select %(idem)s,
                   coalesce(
                       (select max(attempt) from public.episode_pipeline_runs
                        where idempotency_key = %(idem)s),
                       0
                   ) + 1,
                   'running', %(enqueued_at)s, now()
            returning run_id
            """,
            {"idem": idempotency_key, "enqueued_at": enqueued_at},
        )
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError("start_pipeline_run 未回傳 run_id")
    return str(row["run_id"])


async def attach_pipeline_run_episode(run_id: str, episode_id: str) -> None:
    """upsert_episode_node 拿到 episode_id 後補回 forensic row，方便日後對照。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.episode_pipeline_runs set episode_id = %s, updated_at = now() "
            "where run_id = %s",
            (episode_id, run_id),
        )


async def finalize_pipeline_run(
    run_id: str,
    *,
    status: str,
    gen_metrics: dict[str, Any],
    research_metrics: dict[str, Any],
    error: dict[str, Any] | None = None,
) -> None:
    """run_pod 結束（成功或失敗）時關閉 forensic row。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.episode_pipeline_runs
            set status = %s,
                finished_at = now(),
                gen_metrics = %s::jsonb,
                research_metrics = %s::jsonb,
                error = %s::jsonb,
                updated_at = now()
            where run_id = %s
            """,
            (
                status,
                json.dumps(gen_metrics, ensure_ascii=False),
                json.dumps(research_metrics, ensure_ascii=False),
                json.dumps(error, ensure_ascii=False) if error is not None else None,
                run_id,
            ),
        )


async def find_reusable_episode(
    big_topic: str,
    user_id: str,
    *,
    length_tier: str = "medium",
    cefr: str = "B1",
    is_free: bool = True,
) -> str | None:
    """重用核心查詢——單一 anti-join，禁特例分支（PRD §4.5）。

    同大主題 + 同長度 tier + 同 CEFR 等級 + 新鮮度未過期 + 該 user 未聽過 → 取最新一集。
    「過期」與「已交付」都是 WHERE 的一部分，沒有 if/else 拆支。

    Phase 4：加 length_tier WHERE；topic_type 不加（與 length_tier 一起決定 format
    但兩者若同時過濾會把「同 big_topic 不同 entry_mode」的兩條邏輯拆成四種組合，
    且 idempotency_key 已含 topic_type，重用不會撞——見 nodes.upsert_episode_node）。
    CEFR 過濾：A2 使用者不能拿到 B2 集，語言難度是內容契約的一部分。

    is_free：精確選擇公開（True）／私人（False）候選集。私人重用由 caller（resolve_for_user
    的 L2）顯式 opt-in；candidate 級 anti-join 仍保留，保護直接呼叫 repo 或 L1/L2 race。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.id
            from public.episodes e
            where e.big_topic = %(big_topic)s
              and e.length_tier = %(length_tier)s
              and e.cefr_level = %(cefr)s
              and e.is_free = %(is_free)s
              and (e.expires_at is null or now() < e.expires_at)
              and not exists (
                  select 1 from public.deliveries d
                  where d.episode_id = e.id and d.user_id = %(user_id)s
              )
            order by e.created_at desc
            limit 1
            """,
            {
                "big_topic": big_topic,
                "user_id": user_id,
                "length_tier": length_tier,
                "cefr": cefr,
                "is_free": is_free,
            },
        )
        row = await cur.fetchone()
    return str(row["id"]) if row else None


async def has_delivered_episode_for_topic(user_id: str, big_topic: str) -> bool:
    """是否曾交付該 user 任一相同 big_topic 的集數（公開/私人 都算）。

    給 resolve_for_user 的 L3 guard 用——「同 user 同主題已有任一集 → 跳過重用，強制新生成」。
    不過濾 length_tier / cefr_level：任一歷史都算，避免「同人同主題拿兩個版本」。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select exists (
                select 1
                from public.deliveries d
                join public.episodes e on e.id = d.episode_id
                where d.user_id = %(user_id)s
                  and e.big_topic = %(big_topic)s
            ) as has_delivered
            """,
            {"user_id": user_id, "big_topic": big_topic},
        )
        row = await cur.fetchone()
    return bool(row and row["has_delivered"])


async def has_specified_topic_request(user_id: str, big_topic: str) -> bool:
    """是否曾以 specified request 提過相同 raw_topic（fallback 不算）。

    給 resolve_for_user 的 L2 條件用——「caller 過去自己指定過該主題 → 跳過私人重用，
    否則會拿到自己過去的私人集」。`source='specified'` 過濾是必要的：nightly 投影的
    fallback row 來自 onboarding_big_topic，不該視為「曾指定過」。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select exists (
                select 1
                from public.topic_requests tr
                where tr.user_id = %(user_id)s
                  and tr.raw_topic = %(big_topic)s
                  and tr.source = 'specified'
            ) as has_specified
            """,
            {"user_id": user_id, "big_topic": big_topic},
        )
        row = await cur.fetchone()
    return bool(row and row["has_specified"])


async def list_prior_episode_meta(
    user_id: str, big_topic: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """該 user 在同 big_topic 已交付集的 angle 與 extracted_facts（新→舊）。

    給 resolve_for_user 做角度輪替與 avoid_facts 餵入——同主題重複點餐時，
    新集拿舊集的角度避開、facts 避重，不再每次都生「定義」角度的相似內容。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.angle, e.extracted_facts
            from public.episodes e
            join public.deliveries d on d.episode_id = e.id
            where d.user_id = %s and e.big_topic = %s
            order by e.created_at desc
            limit %s
            """,
            (user_id, big_topic, limit),
        )
        rows = await cur.fetchall()
    return [{"angle": r["angle"], "extracted_facts": r["extracted_facts"] or []} for r in rows]


async def insert_delivery(
    user_id: str, episode_id: str, deliver_date: str, *, order_id: str | None = None
) -> bool:
    """建一筆交付（heard-set 權威來源）。重投不報錯（ON CONFLICT DO NOTHING）。

    回傳是否實際新增（False = 早已交付過，冪等略過）。

    order_id：個人點餐專屬（migration 0024），標出這筆交付屬於哪張訂單，解決
    佇列制下「同一天多筆訂單，哪筆交付屬於哪張」的歧義。預設 None——頻道/
    evergreen 路徑不帶，conflict target 用 `unique nulls not distinct` 三欄
    constraint，NULL 之間仍視為相等，dedup 行為與改動前一致。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.deliveries (user_id, episode_id, deliver_date, order_id)
            values (%s, %s, %s, %s)
            on conflict (user_id, episode_id, order_id) do nothing
            returning id
            """,
            (user_id, episode_id, deliver_date, order_id),
        )
        row = await cur.fetchone()
    return row is not None


async def claim_daily_notifications(deliver_date: str, now_hhmm: str) -> list[dict[str, str]]:
    """認領「出餐時間已到、還沒通知過」的交付，回傳每筆的 user_id + episode slug/title。

    UPDATE ... WHERE notified_at is null 同時完成篩選與 atomic claim——cron 掃
    幾次都只會推一次，不需要額外的 marker 表。時間用 <= 而非分鐘精確比對：
    worker 重啟或 cron 漂移不會讓使用者整天收不到。

    一個 user 當天可能有多集（多筆 deliveries），原樣回傳讓 caller 自己 group
    後再發一則整合通知；不要在這層去重，否則第 2~N 集會被吞掉。

    left join + coalesce 讓「還沒有 user_settings 列」不是特殊情況（對齊
    app/routers/settings.py 的 _SELECT 做法）。沒有任何 push 訂閱的 user 直接
    排除，避免白認領（notified_at 被寫掉但沒人收到）。

    inner join episodes 拿到 slug 跟顯示用標題（coalesce title_zh, title），
    讓 caller 直接拼 payload 不用再多查一次。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            with due as (
              select d.id, d.user_id, e.slug,
                     coalesce(e.title_zh, e.title) as title
              from public.deliveries d
              join public.episodes e on e.id = d.episode_id
              left join public.user_settings us on us.user_id = d.user_id
              where d.deliver_date = %s
                and d.notified_at is null
                and coalesce(us.default_delivery_time, '07:00'::time) <= %s::time
                and exists (
                  select 1 from public.push_subscriptions ps where ps.user_id = d.user_id
                )
              order by e.published_at nulls last, d.id
            )
            update public.deliveries d
            set notified_at = now()
            from due
            where d.id = due.id
            returning d.user_id::text as user_id, due.slug, due.title
            """,
            (deliver_date, now_hhmm),
        )
        rows = await cur.fetchall()
        await conn.commit()
    return [{"user_id": r["user_id"], "slug": r["slug"], "title": r["title"]} for r in rows]


async def get_episode_meta(episode_id: str) -> dict[str, str] | None:
    """拿集數的對外顯示資訊（slug + 顯示用標題），給 push 通知拼 payload。

    回 None 表示 episode 不存在（理論上 FK CASCADE 不會發生，caller 端要 guard）。
    title 已經 coalesce title_zh → title，中文版優先。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select slug, coalesce(title_zh, title) as title from public.episodes where id = %s",
            (episode_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return {"slug": row["slug"], "title": row["title"]}


async def undelivered_users(deliver_date: str) -> list[str]:
    """當天還沒收到任何交付的 user（evergreen 兜底對象）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select u.id
            from public.users u
            where not exists (
                select 1 from public.deliveries d
                where d.user_id = u.id and d.deliver_date = %s
            )
            order by u.id
            """,
            (deliver_date,),
        )
        rows = await cur.fetchall()
    return [str(r["id"]) for r in rows]


async def pick_evergreen_episode(big_topic: str | None) -> str | None:
    """挑一集常青兜底集。給了 big_topic 先比對；挑不到就退回任一常青集。

    用 ORDER BY 把「正好同大主題」排到最前，避免 if/else 兩段查詢（消除特例）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.id
            from public.episodes e
            where e.freshness_class = 'evergreen'
              and (e.expires_at is null or now() < e.expires_at)
              and (
                e.audio_r2_keys <> '[]'::jsonb
                or e.audio_r2_key is not null
              )
            order by (e.big_topic is not distinct from %(big_topic)s) desc,
                     e.created_at desc
            limit 1
            """,
            {"big_topic": big_topic},
        )
        row = await cur.fetchone()
    return str(row["id"]) if row else None


