"""每日點餐 router：get / save / list(from,to) / markPlayed / delete。

對映 daily_orders 表（primary key (user_id, order_date)）。
所有查詢以 user_id 收斂。日期字串 'YYYY-MM-DD'。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from app.schemas import MarkPlayedBody, SaveDailyOrderBody
from shared.db import repo
from shared.db.pool import connection
from shared.models import DailyOrder, Episode

router = APIRouter(prefix="/daily-orders", tags=["daily-orders"])

_SELECT = """
  select to_char(order_date, 'YYYY-MM-DD') as date,
         selected_topics, specific_request, status,
         to_char(delivery_time, 'HH24:MI') as delivery_time,
         to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at,
         to_char(updated_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as updated_at,
         to_char(played_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as played_at,
         entry_mode, length_tier
  from public.daily_orders
"""


def _row_to_order(row: dict[str, Any]) -> DailyOrder:
    return DailyOrder.model_validate(row)


@router.get("/{date}", response_model=ApiResponse[DailyOrder | None])
async def get_daily_order(
    date: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[DailyOrder | None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SELECT + " where user_id = %s and order_date = %s", (user_id, date))
        row = await cur.fetchone()
    return ok(_row_to_order(row) if row else None)


@router.get("", response_model=ApiResponse[list[DailyOrder]])
async def list_daily_orders(
    from_date: str, to_date: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[list[DailyOrder]]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            _SELECT + " where user_id = %s and order_date between %s and %s order by order_date",
            (user_id, from_date, to_date),
        )
        rows = await cur.fetchall()
    return ok([_row_to_order(r) for r in rows])


@router.put("", response_model=ApiResponse[DailyOrder])
async def save_daily_order(
    body: SaveDailyOrderBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[DailyOrder]:
    """upsert 整筆訂單（key = user_id + date）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.daily_orders
              (user_id, order_date, selected_topics, specific_request,
               status, delivery_time, played_at,
               entry_mode, length_tier, updated_at)
            values (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, now())
            on conflict (user_id, order_date) do update set
              selected_topics  = excluded.selected_topics,
              specific_request = excluded.specific_request,
              status           = excluded.status,
              delivery_time    = excluded.delivery_time,
              played_at        = excluded.played_at,
              entry_mode       = excluded.entry_mode,
              length_tier      = excluded.length_tier,
              updated_at       = now()
            """,
            (
                user_id,
                body.date,
                json.dumps(body.selected_topics),
                body.specific_request,
                body.status,
                body.delivery_time,
                body.played_at,
                body.entry_mode,
                body.length_tier,
            ),
        )
        await cur.execute(_SELECT + " where user_id = %s and order_date = %s", (user_id, body.date))
        row = await cur.fetchone()
        await conn.commit()
    assert row is not None
    return ok(_row_to_order(row))


@router.post("/{date}/played", response_model=ApiResponse[DailyOrder | None])
async def mark_order_played(
    date: str, body: MarkPlayedBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[DailyOrder | None]:
    """標記已播放。找不到回 null（對齊 mockApi）。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            update public.daily_orders
            set status = 'played', played_at = %s, updated_at = %s
            where user_id = %s and order_date = %s
            returning order_date
            """,
            (body.played_at, body.played_at, user_id, date),
        )
        updated = await cur.fetchone()
        if updated is None:
            await conn.commit()
            return ok(None)
        await cur.execute(_SELECT + " where user_id = %s and order_date = %s", (user_id, date))
        row = await cur.fetchone()
        await conn.commit()
    assert row is not None
    return ok(_row_to_order(row))


@router.delete("/{date}", response_model=ApiResponse[None])
async def delete_daily_order(
    date: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "delete from public.daily_orders where user_id = %s and order_date = %s",
            (user_id, date),
        )
        await conn.commit()
    return ok(None)


@router.get("/{date}/episode", response_model=ApiResponse[Episode | None])
async def get_daily_order_episode(
    date: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[Episode | None]:
    """取當天交付給該 user 的集數，找不到回 null（前端 fallback 到 listEpisodes()[0]）。

    URL 語意：daily_order 是主資源，episode 是其子資源（解決 PlayerRoute 點 ?date=
    連結時不知道播哪集的問題）。
    """
    return ok(await repo.find_delivered_episode(user_id, date))
