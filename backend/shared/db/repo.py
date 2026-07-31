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


async def get_order_status(user_id: str, order_id: str) -> str | None:
    """取某 user 某筆 daily_order 的狀態；查無回 None（與 rowcount=0 區分）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select status from public.daily_orders where user_id = %s and id = %s",
            (user_id, order_id),
        )
        row = await cur.fetchone()
    return str(row["status"]) if row else None


async def get_order_date(order_id: str) -> str | None:
    """取某筆訂單的 order_date（jobs router 組 orchestrate 訊息用）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select to_char(order_date, 'YYYY-MM-DD') as order_date "
            "from public.daily_orders where id = %s",
            (order_id,),
        )
        row = await cur.fetchone()
    return str(row["order_date"]) if row else None


async def transition_order_to_queued(user_id: str, order_id: str) -> bool:
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
            where user_id = %s and id = %s and status = 'pending'
            returning id
            """,
            (user_id, order_id),
        )
        return cur.rowcount > 0


async def deliver_and_mark_ready(
    user_id: str, episode_id: str, deliver_date: str, *, order_id: str
) -> bool:
    """Atomic insert_delivery + 訂單翻 ready，個人點餐交付收尾專用。

    兩個寫入必須同進同退——否則前端輪詢會踩到「deliveries 已寫但
    daily_orders.status 還停在 queued」的不一致窗口，導致使用者卡在
    「這集正在生成中」直到 DB 真的翻 ready 之前都救不回（重整也沒用）。
    把兩段 SQL 包進同一個 conn.transaction() 讓 window 收斂成 0。

    翻牌條件含 expired（遲到交付復活）：單執行緒 worker 排隊超過
    EXPIRE_AFTER_SEC 時訂單會先被 reconcile 退役，pipeline 之後才完成——
    此時 delivery 照樣寫入，訂單一併復活成 ready，使用者拿得到內容。
    expired 不佔 one-active partial index（只涵蓋 pending/queued），
    即使使用者已另開新單也不會撞唯一鍵；DELETE 只允許 pending，
    不存在「復活已刪除訂單」的孤兒問題。

    回傳 inserted 沿用 insert_delivery 語意（True=新寫入、False=ON CONFLICT
    已存在）。order_id 必填——沒有 order_id 的頻道/evergreen 路徑請直接呼叫
    reuse_repo.insert_delivery()，那條路徑不碰訂單狀態。

    FK violation（episode 已不存在）不吞，讓它自然往外冒：呼叫端既有的
    ForeignKeyViolation 例外處理繼續生效，且翻牌會跟著 rollback——
    比改動前的 partial-fail 行為更乾淨。
    """
    async with connection() as conn, conn.transaction():
        async with conn.cursor(row_factory=dict_row) as cur:
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
        inserted = row is not None
        # 條件 UPDATE，queued/expired 才翻、冪等；expired 分支即「遲到交付復活」。
        async with conn.cursor() as cur:
            await cur.execute(
                "update public.daily_orders set status = 'ready', updated_at = now() "
                "where id = %s and status in ('queued', 'expired')",
                (order_id,),
            )
    return inserted


async def list_stuck_pending_orders(older_than_sec: int) -> list[dict[str, Any]]:
    """找 pending 超過門檻卻沒被翻 queued 的訂單（即時觸發 enqueue 失敗）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select id, user_id, to_char(order_date, 'YYYY-MM-DD') as order_date
            from public.daily_orders
            where status = 'pending'
              and updated_at < now() - make_interval(secs => %s)
            """,
            (older_than_sec,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def list_stuck_queued_orders_without_delivery(older_than_sec: int) -> list[dict[str, Any]]:
    """找 queued 超過門檻仍完全沒有交付的訂單（生成真的失敗，需要墊檔兜底）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select o.id, o.user_id, to_char(o.order_date, 'YYYY-MM-DD') as order_date
            from public.daily_orders o
            where o.status = 'queued'
              and o.updated_at < now() - make_interval(secs => %s)
              and not exists (
                select 1 from public.deliveries d where d.order_id = o.id
              )
            """,
            (older_than_sec,),
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def expire_old_active_orders(older_than_sec: int) -> list[dict[str, Any]]:
    """把卡死超過門檻的 active 訂單（pending/queued 且無 delivery）退役為 expired。

    治本解：原本 reconcile 只會重試 enqueue／補 evergreen，兩個都可能失敗
    （worker.py:218-220 pick_evergreen_episode 回 None 直接 continue）。
    沒有 upper bound 的情況下 row 永遠卡在 active，GET /active 永遠回它，
    UI 永遠顯示「這集正在生成中」。

    在 _order_reconcile 第一步呼叫：先退役、再做重放／兜底，被退役的 row
    不會被 reconcile 重複處理。回傳退役清單給 worker log，觀察實際發生率。

    CAS 條件式 UPDATE：只在 pending/queued 翻 expired，不會覆蓋已被別的
    路徑（手動 cancel、剛好 deliver_and_mark_ready 跑完）翻過的狀態。
    單一 SQL 完成選取＋翻牌＋回傳，避免並發下 reconcile 兩輪都打中同一筆。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            update public.daily_orders o
               set status = 'expired', updated_at = now()
              from (
                select id
                  from public.daily_orders
                 where status in ('pending', 'queued')
                   and updated_at < now() - make_interval(secs => %s)
                   and not exists (
                     select 1 from public.deliveries d where d.order_id = public.daily_orders.id
                   )
                 for update skip locked
              ) targets
             where o.id = targets.id
         returning o.id::text as id, o.user_id,
                   to_char(o.order_date, 'YYYY-MM-DD') as order_date, o.status
            """,
            (older_than_sec,),
        )
        rows = await cur.fetchall()
        await conn.commit()
    return [dict(r) for r in rows]


async def find_delivered_episode(user_id: str, order_id: str) -> dict[str, Any] | None:
    """取某筆訂單交付給該 user 的集數原始 row，找不到回 None。

    刻意回傳原始 row 而非組好的 Episode：segments 簽章要呼叫 R2（I/O），
    交給 router 層的 build_episode() 統一組——跟 GET /episodes/{slug} 共用
    同一份組裝邏輯，避免這條路徑漏簽 segments（之前發生過：這裡自己組了一份
    沒帶 segments 的 Episode，前端拿到空 segments 當「舊集未 backfill」處理，
    不報錯但完全靜音，難排查）。

    用 order_id 精準比對（取代舊版 (user_id, deliver_date) 猜測）：佇列制下
    同一天可能有多筆歷史訂單，deliver_date 不再是唯一鍵。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            select e.slug, e.title, e.title_zh, e.topic, e.cefr_level,
                   e.is_free, e.script_json, e.sources,
                   e.audio_r2_key, e.audio_r2_keys
            from public.deliveries d
            join public.episodes e on e.id = d.episode_id
            where d.user_id = %s and d.order_id = %s
            limit 1
            """,
            (user_id, order_id),
        )
        row = await cur.fetchone()
    return dict(row) if row else None
