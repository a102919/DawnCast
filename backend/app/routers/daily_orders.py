"""點餐 router：隨時可點、佇列制（一次一筆進行中，生成完成即解鎖下一筆）。

對映 daily_orders 表（migration 0024 起 PK 改為獨立 id，order_date 降級為一般
欄位）。所有查詢以 user_id 收斂。同一時間只允許一筆進行中（pending/queued）
訂單，由 DB 層 partial unique index 強制（見 migration 0024），撞到時本層接成
409。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from app.schemas import CreateDailyOrderBody, MarkPlayedBody
from app.services.episode_assembly import build_episode
from shared.config import get_settings
from shared.db import repo
from shared.db.pool import connection
from shared.errors import ConflictError, NotFoundError
from shared.models import DailyOrder, Episode

router = APIRouter(prefix="/daily-orders", tags=["daily-orders"])

_SELECT = """
  select id::text as id,
         to_char(order_date, 'YYYY-MM-DD') as date,
         selected_topics, specific_request, status,
         to_char(delivery_time, 'HH24:MI') as delivery_time,
         to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
         to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as updated_at,
         to_char(played_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as played_at,
         entry_mode, length_tier,
         status in ('ready', 'played') as ready
  from public.daily_orders
"""


def _row_to_order(row: dict[str, Any]) -> DailyOrder:
    return DailyOrder.model_validate(row)


def _today_in_app_tz() -> str:
    tz = ZoneInfo(get_settings().app_timezone)
    return datetime.now(tz).date().isoformat()


@router.post("", status_code=201, response_model=ApiResponse[DailyOrder])
async def create_daily_order(
    body: CreateDailyOrderBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[DailyOrder]:
    """建立新訂單。已有進行中訂單時，partial unique index 撞 UniqueViolation → 409。"""
    order_date = _today_in_app_tz()
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        try:
            await cur.execute(
                """
                insert into public.daily_orders
                  (user_id, order_date, selected_topics, specific_request,
                   status, delivery_time, entry_mode, length_tier)
                values (%s, %s, %s::jsonb, %s, 'pending', %s, %s, %s)
                returning id
                """,
                (
                    user_id,
                    order_date,
                    json.dumps(body.selected_topics),
                    body.specific_request,
                    body.delivery_time,
                    body.entry_mode,
                    body.length_tier,
                ),
            )
        except UniqueViolation:
            raise ConflictError("尚有訂單處理中，請等目前訂單完成後再點新的") from None
        created = await cur.fetchone()
        assert created is not None
        await cur.execute(_SELECT + " where id = %s", (created["id"],))
        row = await cur.fetchone()
        await conn.commit()
    assert row is not None
    return ok(_row_to_order(row))


@router.get("/active", response_model=ApiResponse[DailyOrder | None])
async def get_active_order(
    user_id: str = Depends(get_current_user),
) -> ApiResponse[DailyOrder | None]:
    """目前進行中（pending/queued）的訂單，沒有回 null。

    註冊順序：必須在 GET /{order_id} 之前，否則 'active' 會被當成 order_id 吃掉。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            _SELECT + " where user_id = %s and status in ('pending', 'queued') "
            "order by created_at desc limit 1",
            (user_id,),
        )
        row = await cur.fetchone()
    return ok(_row_to_order(row) if row else None)


@router.get("/history", response_model=ApiResponse[list[DailyOrder]])
async def list_order_history(
    limit: int = 20, before: str | None = None, user_id: str = Depends(get_current_user)
) -> ApiResponse[list[DailyOrder]]:
    """已生成完成（ready）、已播放（played）或已退役（expired）的訂單，cursor 分頁。

    生成完成即解鎖下一筆訂單，不用等實際播放完——ready 訂單也算「進來歷史」。
    expired 也算「進來歷史」：reconcile 退役的卡死訂單，使用者看得到「這集被
    放棄了」而不是憑空消失，debug 線索也保留在 DB。
    註冊順序：必須在 GET /{order_id} 之前，理由同 /active。
    """
    page_size = max(1, min(limit, 100))
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if before:
            await cur.execute(
                _SELECT + " where user_id = %s and status in ('ready', 'played', 'expired') "
                "and created_at < %s order by created_at desc limit %s",
                (user_id, before, page_size),
            )
        else:
            await cur.execute(
                _SELECT + " where user_id = %s and status in ('ready', 'played', 'expired') "
                "order by created_at desc limit %s",
                (user_id, page_size),
            )
        rows = await cur.fetchall()
    return ok([_row_to_order(r) for r in rows])


@router.get("/{order_id}", response_model=ApiResponse[DailyOrder | None])
async def get_daily_order(
    order_id: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[DailyOrder | None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SELECT + " where user_id = %s and id = %s", (user_id, order_id))
        row = await cur.fetchone()
    return ok(_row_to_order(row) if row else None)


@router.post("/{order_id}/played", response_model=ApiResponse[DailyOrder | None])
async def mark_order_played(
    order_id: str, body: MarkPlayedBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[DailyOrder | None]:
    """標記已播放。找不到回 null（對齊 mockApi）。

    只允許 ready/played → played：無守衛時前端可把 pending/queued 洗成
    played 繞過 one-active 限制（生成中的訂單變孤兒），expired 也不該
    被標成已播放。played 重複標記冪等，coalesce 保留首次播放時間。
    比照 delete_daily_order：條件 UPDATE 判斷成功與否，避免 TOCTOU。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            update public.daily_orders
            set status = 'played', played_at = coalesce(played_at, %s), updated_at = %s
            where user_id = %s and id = %s and status in ('ready', 'played')
            returning id
            """,
            (body.played_at, body.played_at, user_id, order_id),
        )
        updated = await cur.fetchone()
        if updated is not None:
            await cur.execute(_SELECT + " where id = %s", (updated["id"],))
            row = await cur.fetchone()
            await conn.commit()
            assert row is not None
            return ok(_row_to_order(row))
        await cur.execute(
            "select status from public.daily_orders where user_id = %s and id = %s",
            (user_id, order_id),
        )
        existing = await cur.fetchone()
        await conn.commit()
    if existing is None:
        return ok(None)
    raise ConflictError("訂單尚未生成完成，無法標記播放")


@router.delete("/{order_id}", response_model=ApiResponse[None])
async def delete_daily_order(
    order_id: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    """只允許取消 pending 訂單。queued 已經開始生成，硬刪會產生孤兒交付 → 409。

    單一原子 DELETE...WHERE status='pending' 判斷成功與否，避免 select 後
    再 delete 的 TOCTOU（並發下 jobs router 可能剛好把它翻成 queued）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.daily_orders "
            "where user_id = %s and id = %s and status = 'pending' returning id",
            (user_id, order_id),
        )
        deleted = await cur.fetchone()
        if deleted is not None:
            await conn.commit()
            return ok(None)
        await cur.execute(
            "select status from public.daily_orders where user_id = %s and id = %s",
            (user_id, order_id),
        )
        existing = await cur.fetchone()
        await conn.commit()
    if existing is None:
        raise NotFoundError("查無此訂單")
    raise ConflictError("訂單已開始生成，無法取消")


@router.get("/{order_id}/episode", response_model=ApiResponse[Episode | None])
async def get_daily_order_episode(
    order_id: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[Episode | None]:
    """取這筆訂單交付給該 user 的集數，找不到回 null（前端 fallback 到 listEpisodes()[0]）。

    URL 語意：daily_order 是主資源，episode 是其子資源（解決 PlayerRoute 導頁時
    不知道播哪集的問題）。用 order_id 精準比對，取代舊版用日期猜的寫法——
    佇列制下同一天可能有多筆歷史訂單，日期不再是唯一鍵。
    """
    row = await repo.find_delivered_episode(user_id, order_id)
    return ok(await build_episode(row["slug"], row) if row else None)
