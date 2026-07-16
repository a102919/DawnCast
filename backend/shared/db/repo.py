"""引擎 / 批次層的參數化 SQL repo（全用 shared.db.pool.connection）。

app 層有自己的查詢（授權收斂），這裡只放夜間 pipeline 需要的寫入與重用查詢。
SQL 全參數化，禁字串拼接。重用查詢核心是單一 anti-join（見 reuse.py），
不在這層做特例分支——讓「過期」與「已交付」都收斂成同一條 WHERE。
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from shared.db.pool import connection
from shared.models import Cue, Episode


async def project_orders_to_requests(request_date: str) -> int:
    """把當天 daily_orders 投影成 topic_requests（PRD §4.2、Phase 4）。

    selected_topics + specific_request 併成 raw_topic；兩者皆空時標 source='fallback'，
    並用 users.onboarding_big_topic 當題目來源（消除「沒下單」這個特殊情況）。
    回傳投影出的列數。冪等：先刪掉當天已投影的列再重投。

    Phase 4 新增：把 daily_orders.entry_mode 帶到 topic_requests.topic_type，
    daily_orders.length_tier 也一併帶入。fallback 情況下使用者選的 length_tier
    仍帶過去（不退回 medium），因為這是使用者意圖的一部分，不是系統猜的。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.topic_requests where request_date = %s",
            (request_date,),
        )
        # array_to_string + nullif 把 selected_topics（jsonb 陣列）與 specific_request
        # 併成單一 raw_topic；兩者皆空→ NULL → source='fallback'、raw 用大主題。
        # 同樣的 concat 邏輯重複兩次（用在 topic_type=entry_mode 判定的同一個 case 表達式，
        # 重新算出來不額外加 CTE，保持單一 INSERT 一眼看懂）。
        await cur.execute(
            """
            insert into public.topic_requests
                (user_id, request_date, raw_topic, source, topic_type, length_tier)
            select
                o.user_id,
                o.order_date,
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
                end as source,
                o.entry_mode as topic_type,
                o.length_tier as length_tier
            from public.daily_orders o
            join public.users u on u.id = o.user_id
            where o.order_date = %s
            """,
            (request_date,),
        )
        return cur.rowcount


async def list_requests_for_date(request_date: str) -> list[dict[str, Any]]:
    """取當天投影出的 topic_requests，供 orchestrate 逐筆跑重用。

    big_topic 用 raw_topic 當大方向分桶 key（MVP 不接向量聚類，見 reuse.py）。
    raw_topic 為 NULL 的略過（理論上 fallback 已用 onboarding_big_topic 補過）。

    Phase 4：多帶 topic_type / length_tier 給 _orchestrate，傳給 resolve_for_user
    與 find_reusable_episode。format 是 derived，跳過不重複帶。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select user_id::text as user_id,
                   raw_topic as big_topic,
                   topic_type,
                   length_tier
            from public.topic_requests
            where request_date = %s and raw_topic is not null
            order by user_id
            """,
            (request_date,),
        )
        rows = await cur.fetchall()
    return [
        {
            "user_id": r["user_id"],
            "big_topic": r["big_topic"],
            "topic_type": r["topic_type"],
            "length_tier": r["length_tier"],
        }
        for r in rows
    ]


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
) -> tuple[str, bool]:
    """建一列 episodes（媒體 key / cues 之後用 update_episode_keys 補）。

    回傳 (episode_id, already_rendered)：
      - 冪等鍵未衝突 → 新建列，already_rendered=False。
      - 衝突（同 key 已存在）→ 復用既有列，避免重投時重複建集與 R2 孤兒物件。
        already_rendered = 既有列是否已渲染完成（audio_r2_key 非空），
        讓上層跳過重渲染、只補交付。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.episodes
                (slug, title, title_zh, topic, cefr_level,
                 big_topic, angle, freshness_class, source_cluster_id,
                 idempotency_key, length_tier, format, grounded,
                 input_tokens, output_tokens)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    audio_key: str | None,
    mp4_key: str | None,
    srt_key: str | None,
    script_json: dict[str, Any],
    cues: list[Cue],
    extracted_facts: list[dict[str, Any]] | None = None,
    target_vocab: list[dict[str, Any]] | None = None,
) -> None:
    """渲染完成後回填媒體 key 與內容。script_json 內含 cues（前端播放頁吃這個）。"""
    payload = dict(script_json)
    payload["cues"] = [c.model_dump(by_alias=False) for c in cues]
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            update public.episodes
            set audio_r2_key = %s,
                mp4_r2_key = %s,
                srt_r2_key = %s,
                script_json = %s::jsonb,
                extracted_facts = %s::jsonb,
                target_vocab = %s::jsonb
            where id = %s
            """,
            (
                audio_key,
                mp4_key,
                srt_key,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(extracted_facts, ensure_ascii=False)
                if extracted_facts is not None
                else None,
                json.dumps(target_vocab, ensure_ascii=False) if target_vocab is not None else None,
                episode_id,
            ),
        )


async def find_reusable_episode(
    big_topic: str,
    user_id: str,
    *,
    length_tier: str = "medium",
) -> str | None:
    """重用核心查詢——單一 anti-join，禁特例分支（PRD §4.5）。

    同大主題 + 同長度 tier + 新鮮度未過期 + 該 user 未聽過 → 取最新一集。
    「過期」與「已交付」都是 WHERE 的一部分，沒有 if/else 拆支。

    Phase 4：加 length_tier WHERE；topic_type 不加（與 length_tier 一起決定 format
    但兩者若同時過濾會把「同 big_topic 不同 entry_mode」的兩條邏輯拆成四種組合，
    且 idempotency_key 已含 topic_type，重用不會撞——見 nodes.upsert_episode_node）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.id
            from public.episodes e
            where e.big_topic = %(big_topic)s
              and e.length_tier = %(length_tier)s
              and (e.expires_at is null or now() < e.expires_at)
              and not exists (
                  select 1 from public.deliveries d
                  where d.episode_id = e.id and d.user_id = %(user_id)s
              )
            order by e.created_at desc
            limit 1
            """,
            {"big_topic": big_topic, "user_id": user_id, "length_tier": length_tier},
        )
        row = await cur.fetchone()
    return str(row["id"]) if row else None


async def insert_delivery(user_id: str, episode_id: str, deliver_date: str) -> bool:
    """建一筆交付（heard-set 權威來源）。重投不報錯（ON CONFLICT DO NOTHING）。

    回傳是否實際新增（False = 早已交付過，冪等略過）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.deliveries (user_id, episode_id, deliver_date)
            values (%s, %s, %s)
            on conflict (user_id, episode_id) do nothing
            returning id
            """,
            (user_id, episode_id, deliver_date),
        )
        row = await cur.fetchone()
    return row is not None


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
              and e.audio_r2_key is not null
            order by (e.big_topic is not distinct from %(big_topic)s) desc,
                     e.created_at desc
            limit 1
            """,
            {"big_topic": big_topic},
        )
        row = await cur.fetchone()
    return str(row["id"]) if row else None


async def mark_orders_status_for_date(
    request_date: str, *, from_status: str, to_status: str
) -> int:
    """把當天所有 from_status 訂單翻成 to_status（collect_open 22:00 用）。

    WHERE 帶 status=from_status，避免覆蓋已 played 的列；冪等（重跑 rowcount=0 視為正常）。
    回傳實際更新的列數給 logger。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.daily_orders
            set status = %s, updated_at = now()
            where order_date = %s and status = %s
            """,
            (to_status, request_date, from_status),
        )
        return cur.rowcount


async def get_order_status(user_id: str, order_date: str) -> str | None:
    """取某 user 某日期的 daily_order 狀態；查無回 None（與 rowcount=0 區分）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select status from public.daily_orders where user_id = %s and order_date = %s",
            (user_id, order_date),
        )
        row = await cur.fetchone()
    return str(row["status"]) if row else None


async def transition_order_to_queued(user_id: str, order_date: str) -> bool:
    """原子把 daily_order.status 從 pending 翻 queued（jobs router 觸發用）。

    SQL 層 CAS：UPDATE ... WHERE status='pending' RETURNING。
    並發兩個請求時第二個會等第一個 row lock 釋放後看到 status='queued'，
    rowcount=0 → 回傳 False → router 翻譯成 409。
    不需任何應用層鎖；零跨 process 風險。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.daily_orders
            set status = 'queued', updated_at = now()
            where user_id = %s and order_date = %s and status = 'pending'
            returning order_date
            """,
            (user_id, order_date),
        )
        return cur.rowcount > 0


def _cues_from_script_json(script_json: Any) -> list[Cue]:
    """script_json 可能是 {cues:[...]} 或直接 [...]，皆容錯。"""
    if not script_json:
        return []
    raw = script_json.get("cues") if isinstance(script_json, dict) else script_json
    if not isinstance(raw, list):
        return []
    return [Cue.model_validate(c) for c in raw]


async def find_delivered_episode(
    user_id: str, deliver_date: str
) -> Episode | None:
    """取當天交付給該 user 的集數，找不到回 None。

    undelivered_users 的 NOT EXISTS 邏輯保證同 user+date 至多一列；
    deliveries 表本身沒有 created_at，故不加 ORDER BY（Postgres 取任意列即可）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.slug, e.title, e.title_zh, e.topic, e.cefr_level,
                   e.is_free, e.script_json, e.mp4_r2_key, e.audio_r2_key
            from public.deliveries d
            join public.episodes e on e.id = d.episode_id
            where d.user_id = %s and d.deliver_date = %s
            limit 1
            """,
            (user_id, deliver_date),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    return Episode(
        id=row["slug"],
        title=row["title"],
        title_zh=row["title_zh"],
        topic=row["topic"],
        cefr_level=row["cefr_level"],
        is_free=row["is_free"],
        cues=_cues_from_script_json(row["script_json"]),
    )
