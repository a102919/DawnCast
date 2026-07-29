"""參數化 SQL repo（全用 shared.db.pool.connection），app 與 engine 共用的邊界查詢。

夜間 pipeline 專用的投影 / episode upsert / 重用決策等查詢已搬到
engine/pipeline/reuse_repo.py（engine-only，不留在這層）。這裡只放：
  - app 層直接呼叫的訂單狀態轉移 / 交付查詢（daily_orders、jobs router）。
  - slug↔episode id 這類 app 多個 router 共用的底層查詢。
  - push_subscriptions 的 list / 失效清理：雖然目前只有 shared/push.py 的
    notify_user 會用到、而 notify_user 只被 engine/ 層呼叫，但 shared/push.py
    本身是 shared 層模組，搬進 engine/ 會讓 shared → engine 倒過來依賴，
    所以刻意留在這裡。
SQL 全參數化，禁字串拼接。
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row

from shared.db.pool import connection


async def resolve_episode_id(slug: str) -> str | None:
    """slug → episode uuid 底層查詢；查無回 None。

    favorites router（找不到要 404）與 vocab router（找不到仍可入本，只是
    source_episode_id 存 null）的 slug→uuid 查詢語意不同，容錯與否由呼叫端
    自己決定，這裡只負責查詢本身。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select id from public.episodes where slug = %s", (slug,))
        row = await cur.fetchone()
    return str(row["id"]) if row else None


async def upsert_push_subscription(user_id: str, endpoint: str, p256dh: str, auth: str) -> None:
    """登錄 / 更新這台裝置的 push 訂閱。

    endpoint 是 PK（push service 保證全域唯一）；do update 帶 user_id 條件，
    避免有人拿別人的 endpoint 呼叫 subscribe 把該列搶過來。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.push_subscriptions (user_id, endpoint, p256dh, auth)
            values (%s, %s, %s, %s)
            on conflict (endpoint) do update
              set p256dh = excluded.p256dh, auth = excluded.auth
              where push_subscriptions.user_id = %s
            """,
            (user_id, endpoint, p256dh, auth, user_id),
        )
        await conn.commit()


async def delete_push_subscription(user_id: str, endpoint: str) -> None:
    """關閉這台裝置的通知。找不到列不是錯誤（冪等）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.push_subscriptions where user_id = %s and endpoint = %s",
            (user_id, endpoint),
        )
        await conn.commit()


async def list_push_subscriptions(user_id: str) -> list[dict[str, str]]:
    """該 user 所有裝置的訂閱（endpoint + 加密金鑰）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select endpoint, p256dh, auth from public.push_subscriptions where user_id = %s",
            (user_id,),
        )
        rows = await cur.fetchall()
    return [{"endpoint": r["endpoint"], "p256dh": r["p256dh"], "auth": r["auth"]} for r in rows]


async def delete_push_endpoints(endpoints: list[str]) -> None:
    """清掉 push service 回 404/410 的失效訂閱。"""
    if not endpoints:
        return
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.push_subscriptions where endpoint = any(%s)",
            (endpoints,),
        )
        await conn.commit()


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


async def find_delivered_episode(user_id: str, deliver_date: str) -> dict[str, Any] | None:
    """取當天交付給該 user 的集數原始 row，找不到回 None。

    刻意回傳原始 row 而非組好的 Episode：segments 簽章要呼叫 R2（I/O），
    交給 router 層的 build_episode() 統一組——跟 GET /episodes/{slug} 共用
    同一份組裝邏輯，避免這條路徑漏簽 segments（之前發生過：這裡自己組了一份
    沒帶 segments 的 Episode，前端拿到空 segments 當「舊集未 backfill」處理，
    不報錯但完全靜音，難排查）。

    undelivered_users 的 NOT EXISTS 邏輯保證同 user+date 至多一列；
    deliveries 表本身沒有 created_at，故不加 ORDER BY（Postgres 取任意列即可）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.slug, e.title, e.title_zh, e.topic, e.cefr_level,
                   e.is_free, e.script_json, e.sources,
                   e.audio_r2_key, e.audio_r2_keys
            from public.deliveries d
            join public.episodes e on e.id = d.episode_id
            where d.user_id = %s and d.deliver_date = %s
            limit 1
            """,
            (user_id, deliver_date),
        )
        row = await cur.fetchone()
    return dict(row) if row else None
