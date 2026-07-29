"""頻道（Channel）機制參數化 SQL repo。app（admin router）與 engine（worker /
選題 / 每日排程）共用的邊界查詢全收斂在這裡，風格比照 shared/db/repo.py：
`async with connection() as conn, conn.cursor(row_factory=dict_row) as cur`，
SQL 全參數化，禁字串拼接。

── 給後續呼叫端的契約 ──────────────────────────────────────────────

1. `generate` 佇列訊息（既有欄位之外）會多帶三個頻道相關欄位：
     - `channel_id: str | None`         — 這集屬於哪個頻道；None＝非頻道集
       （daily_batch 固定 slot、使用者點餐集），維持既有行為不變。
     - `channel_topic_id: str | None`   — 對應 channel_topics.id；生成成功後
       呼叫端要回填 `update_topic_status(topic_id, "published", episode_id=...)`，
       生成失敗則不動（該選題留在原狀態，下次排程還會再被 pick_daily_topics
       選到，等於自動重試）。
     - `series_context: list[str]`      — 該頻道最近 2~3 集標題（見
       `list_recent_channel_episodes`），供寫稿 prompt 呼應系列脈絡用。

2. 生成端的典型呼叫順序：
     `pick_daily_topics()` 選出今天要生的候選
       → 逐筆組 generate 訊息（帶上面三欄）送佇列
       → worker 生成成功 → `update_topic_status(topic_id, "published", episode_id=new_id)`
         + `mark_channel_published(channel_id, deliver_date)`
       → 選題庫存量低於 `Settings.channel_backlog_target`（見 `count_candidates`）
         時才觸發下一輪選題 LLM，寫回 `insert_channel_topics`。

3. `pick_daily_topics` 的 SQL 是機制核心（同頻道一天最多一集的
   `distinct on`、飢餓因子、候選不足就少產甚至不產），直接照抄自
   channels 機制設計，不要自行改寫；語意見函式 docstring。
"""

from __future__ import annotations

from typing import Any

from psycopg import sql
from psycopg.rows import dict_row

from shared.db.pool import connection

# channels 對外查詢固定帶出的欄位：episode_count / candidate_count 是 admin
# 視圖（Channel model）需要的經營指標，用相關子查詢一次算好，避免呼叫端
# 自己在 Python 迴圈裡對每個頻道再各發兩條查詢（N+1）。
_CHANNEL_COLUMNS = """
    c.id, c.slug, c.name, c.description, c.theme_prompt, c.topic, c.topic_type,
    c.length_tier, c.cefr_level, c.target_interval_days, c.status, c.cover_r2_key,
    c.last_published_at, c.created_at, c.updated_at,
    (select count(*) from public.episodes e
      where e.channel_id = c.id) as episode_count,
    (select count(*) from public.channel_topics ct
      where ct.channel_id = c.id and ct.status = 'candidate') as candidate_count
"""

# update_channel(**fields) 的白名單：只允許改這些欄位。id / created_at 不該被
# 外部呼叫端改動；白名單同時擋掉打錯欄位名卻被 **fields 悄悄吃掉的情況
# （fail fast 優於 silent no-op）。
_CHANNEL_UPDATABLE_FIELDS = frozenset(
    {
        "slug",
        "name",
        "description",
        "theme_prompt",
        "topic",
        "topic_type",
        "length_tier",
        "cefr_level",
        "target_interval_days",
        "status",
        "cover_r2_key",
        "last_published_at",
    }
)


async def list_channels(*, status: str | None = None) -> list[dict[str, Any]]:
    """列出頻道（admin 用），可選依 status 過濾。status=None 回全部。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_CHANNEL_COLUMNS}
            from public.channels c
            where (%(status)s::text is null or c.status = %(status)s)
            order by c.created_at desc
            """,
            {"status": status},
        )
        rows = await cur.fetchall()
    return list(rows)


async def get_channel(channel_id: str) -> dict[str, Any] | None:
    """依 id 取單一頻道；查無回 None。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_CHANNEL_COLUMNS} from public.channels c where c.id = %s",
            (channel_id,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_channel_by_slug(slug: str) -> dict[str, Any] | None:
    """依 slug 取單一頻道（使用者端頁面用）；查無回 None。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_CHANNEL_COLUMNS} from public.channels c where c.slug = %s",
            (slug,),
        )
        row = await cur.fetchone()
    return dict(row) if row else None


async def create_channel(
    *,
    slug: str,
    name: str,
    theme_prompt: str,
    topic: str,
    description: str | None = None,
    topic_type: str = "evergreen",
    length_tier: str = "medium",
    cefr_level: str = "B1",
    target_interval_days: int = 3,
    status: str = "active",
) -> str:
    """建立新頻道，回傳新列 id。

    參數預設值刻意對齊 migration 0021 的欄位預設——DB 預設是最後防線，這裡
    顯式重複一份是讓呼叫端（admin router）不用翻 schema 就知道「不填會怎樣」；
    兩邊若不同步由 test_channels_repo.py 顧。slug 撞到既有頻道會讓底層
    UniqueViolation 往上炸，由呼叫端（router 層）決定要接成 409 還是別的。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.channels
                (slug, name, description, theme_prompt, topic, topic_type,
                 length_tier, cefr_level, target_interval_days, status)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                slug,
                name,
                description,
                theme_prompt,
                topic,
                topic_type,
                length_tier,
                cefr_level,
                target_interval_days,
                status,
            ),
        )
        row = await cur.fetchone()
    if row is None:  # insert ... returning 理論上必回一列
        raise RuntimeError("create_channel 未回傳 id")
    return str(row["id"])


async def update_channel(channel_id: str, **fields: Any) -> bool:
    """部分更新頻道欄位；回傳是否真的改到列（找不到 id 回 False）。

    **fields 只接受 _CHANNEL_UPDATABLE_FIELDS 白名單內的欄位，其餘一律
    ValueError（内部呼叫端打錯欄位名要當場炸，不要默默 no-op）。

    用 psycopg.sql.Identifier／Placeholder 組動態 SET 子句——欄位「名稱」
    經白名單驗證後才進 SQL 文字，欄位「值」全程走 %(...)s 綁定參數，兩者
    分開處理，不是字串拼接使用者資料進 SQL（那才是 coding-rules 禁止的
    情況）。沒有任何欄位要改時直接回 False，不發查詢。
    """
    unknown = set(fields) - _CHANNEL_UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"update_channel 不支援的欄位：{sorted(unknown)}")
    if not fields:
        return False

    assignments = sql.SQL(", ").join(
        sql.SQL("{} = {}").format(sql.Identifier(key), sql.Placeholder(key)) for key in fields
    )
    query = sql.SQL(
        "update public.channels set {assignments}, updated_at = now() where id = %(channel_id)s"
    ).format(assignments=assignments)

    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(query.as_string(None), {**fields, "channel_id": channel_id})
        return cur.rowcount > 0


async def set_channel_cover(channel_id: str, r2_key: str) -> None:
    """寫入封面 R2 key（上傳流程專用的窄接口，呼叫端已在更早步驟確認過
    channel_id 存在，這裡不重複檢查、不回傳 rowcount）。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.channels set cover_r2_key = %s, updated_at = now() where id = %s",
            (r2_key, channel_id),
        )


# ── 選題庫 ──────────────────────────────────────────────────────────


async def list_channel_topics(
    channel_id: str, *, status: str | None = None
) -> list[dict[str, Any]]:
    """該頻道的選題庫，依 score 由高到低排序；status=None 回全部狀態。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select id, channel_id, canonical_topic, angle, rationale, score,
                   status, parent_episode_id, episode_id, created_at, decided_at
            from public.channel_topics
            where channel_id = %(channel_id)s
              and (%(status)s::text is null or status = %(status)s)
            order by score desc, created_at desc
            """,
            {"channel_id": channel_id, "status": status},
        )
        rows = await cur.fetchall()
    return list(rows)


async def insert_channel_topics(channel_id: str, candidates: list[dict[str, Any]]) -> int:
    """批次寫入候選主題，回傳這次呼叫「實際插入」的筆數。

    candidates 每筆字典需含 canonical_topic / angle；rationale / score /
    parent_episode_id 可省略（分別預設 None / 0.0 / None，對齊 DDL 欄位預設）。

    用 on conflict (channel_id, lower(canonical_topic)) do nothing 天然去重——
    選題 LLM 重複建議同一個主題（大小寫不同也算重複）時直接被吃掉。回傳值刻意
    是「真的插入幾筆」而非「傳入幾筆」：呼叫端（選題 LLM 迴圈）要用這個數字
    判斷這輪選題有沒有實質貢獻新主題，而不是誤以為候選庫多了 len(candidates) 筆。
    """
    if not candidates:
        return 0

    row_placeholder = "(%s, %s, %s, %s, %s, %s)"
    values_sql = ", ".join([row_placeholder] * len(candidates))
    params: list[Any] = []
    for c in candidates:
        params.extend(
            [
                channel_id,
                c["canonical_topic"],
                c["angle"],
                c.get("rationale"),
                c.get("score", 0.0),
                c.get("parent_episode_id"),
            ]
        )

    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            insert into public.channel_topics
                (channel_id, canonical_topic, angle, rationale, score, parent_episode_id)
            values {values_sql}
            on conflict (channel_id, lower(canonical_topic)) do nothing
            returning id
            """,
            params,
        )
        rows = await cur.fetchall()
    return len(rows)


async def update_topic_status(
    topic_id: str, status: str, *, episode_id: str | None = None
) -> bool:
    """轉移選題狀態（scheduled/published/rejected/stale），回傳是否真的改到列。

    decided_at 蓋成 now()（記下這次狀態轉移的時間）。episode_id 用 coalesce
    保留既有值：只有生成成功回填 published 時才會帶非 None 進來，其餘轉移
    （rejected/stale/scheduled）傳 None，不會把已經寫好的 episode_id 洗掉。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.channel_topics
            set status = %s,
                episode_id = coalesce(%s, episode_id),
                decided_at = now()
            where id = %s
            """,
            (status, episode_id, topic_id),
        )
        return cur.rowcount > 0


async def count_candidates(channel_id: str) -> int:
    """該頻道目前 candidate 狀態的選題庫存量，給 Settings.channel_backlog_target 比對。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select count(*) as n from public.channel_topics
            where channel_id = %s and status = 'candidate'
            """,
            (channel_id,),
        )
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


# ── 選題所需的頻道歷史 ────────────────────────────────────────────────


async def list_recent_channel_episodes(channel_id: str, limit: int = 8) -> list[dict[str, Any]]:
    """該頻道最近發布的集數（新→舊），供選題 prompt 與 series_context 使用。

    回傳 slug/title/angle/extracted_facts：
      - slug/title 給 series_context（generate 訊息帶最近 2~3 集標題，讓
        寫稿文案呼應系列脈絡）；
      - angle/extracted_facts 給選題 LLM 避免角度重複、事實重複——跟
        engine/pipeline/reuse_repo.py::list_prior_episode_meta 同精神，
        只是這裡以頻道分桶，而非 user + big_topic。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select slug, title, angle, extracted_facts
            from public.episodes
            where channel_id = %s
            order by created_at desc
            limit %s
            """,
            (channel_id, limit),
        )
        rows = await cur.fetchall()
    return list(rows)


# ── 生產端排程查詢 ───────────────────────────────────────────────────


async def pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
    """挑出今天要排程生成的頻道選題。機制核心，SQL 照搬設計，勿自行改寫。

    三個規則共同決定「今天誰上」：
      1. `distinct on (c.id)`：同一頻道一天最多貢獻 1 筆候選，避免單一高分
         頻道把當日名額全部佔滿，逼其餘頻道永遠排不到。
      2. 飢餓因子 `least(3.0, 距上次發布天數 / target_interval_days)` 乘上
         score 當排序用的 priority：距離自己的目標間隔越久沒發布，priority
         被放大越多（上限 3 倍，避免長期停更的頻道一回歸就無限期霸榜），
         讓慢節奏／冷門頻道不會被熱門頻道長期壓著排不到。
      3. 候選不足（不到 max_slots 筆，甚至 0 筆）就照實回傳少於 max_slots
         筆——「沒內容就不產」是刻意的設計，不是 bug；上層不該為了湊數硬塞
         低分主題進生成佇列。

    篩選條件：頻道 status='active'、選題 status='candidate'、score >= 門檻
    （對齊 Settings.channel_min_topic_score），且該頻道已經到了 / 超過
    target_interval_days（或從未發布過）才有資格入選。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            with eligible as (
              select distinct on (c.id)
                     ct.id as topic_id, ct.canonical_topic, ct.angle, ct.parent_episode_id,
                     c.id as channel_id, c.slug as channel_slug, c.topic, c.topic_type,
                     c.length_tier, c.cefr_level,
                     ct.score * least(3.0,
                       coalesce(current_date - c.last_published_at, 9999)::real
                       / c.target_interval_days) as priority
                from public.channel_topics ct
                join public.channels c on c.id = ct.channel_id
               where ct.status = 'candidate'
                 and ct.score >= %s
                 and c.status = 'active'
                 and (c.last_published_at is null
                      or current_date - c.last_published_at >= c.target_interval_days)
               order by c.id, ct.score desc
            )
            select * from eligible order by priority desc limit %s
            """,
            (min_score, max_slots),
        )
        rows = await cur.fetchall()
    return list(rows)


# ── 生成完成後回填 ───────────────────────────────────────────────────


async def mark_channel_published(channel_id: str, deliver_date: str) -> None:
    """生成成功後回填 channels.last_published_at，供下次 pick_daily_topics 的
    target_interval_days 判斷與飢餓因子計算使用。

    用 greatest(...) 而非直接覆寫：理論上呼叫端只會在真的成功產出當天才
    呼叫，但 greatest 兜底可避免任何非預期的重放（例如訊息重投）把日期
    往回撥，讓飢餓因子被錯誤放大。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.channels
            set last_published_at = greatest(coalesce(last_published_at, %s::date), %s::date),
                updated_at = now()
            where id = %s
            """,
            (deliver_date, deliver_date, channel_id),
        )


async def next_episode_no(channel_id: str) -> int:
    """該頻道下一集的 episode_no（1 起跳；該頻道目前沒有任何集數時回 1）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select coalesce(max(episode_no), 0) + 1 as next_no
            from public.episodes where channel_id = %s
            """,
            (channel_id,),
        )
        row = await cur.fetchone()
    return int(row["next_no"]) if row else 1


# ── 使用者訂閱（user_channel_subscriptions）──────────────────────────


async def subscribe(user_id: str, channel_id: str) -> None:
    """訂閱頻道；已訂閱過再呼叫一次是 no-op（PK 是 (user_id, channel_id)）。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.user_channel_subscriptions (user_id, channel_id)
            values (%s, %s)
            on conflict do nothing
            """,
            (user_id, channel_id),
        )
        await conn.commit()


async def unsubscribe(user_id: str, channel_id: str) -> None:
    """取消訂閱；本來就沒訂閱時刪 0 列，同樣視為成功（冪等）。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            delete from public.user_channel_subscriptions
            where user_id = %s and channel_id = %s
            """,
            (user_id, channel_id),
        )
        await conn.commit()


async def list_subscribed_channels(user_id: str) -> list[dict[str, Any]]:
    """該使用者追蹤的頻道（使用者端首頁／頻道頁用）。

    刻意不篩 status='active'：使用者主動追蹤的頻道被 admin 暫停/封存時不該悄悄從清單
    消失，那會讓「我明明追蹤了，怎麼不見了」的疑惑無從解釋起。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_CHANNEL_COLUMNS}
            from public.channels c
            join public.user_channel_subscriptions s on s.channel_id = c.id
            where s.user_id = %s
            order by s.created_at desc
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
    return list(rows)
