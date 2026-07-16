"""Activity router：學習進度上雲（T2）— streak / 聆聽分鐘 / 查詞次數 /

已聽集數 / 播放進度跨裝置同步。

無列回預設（仿 settings.py）。PATCH 是「合併」語意，不是「取代」：
streak_dates / listened_episode_ids 去重，listen_minutes / lookup_count
依月份 key 遞增，last_played 只在新時間戳記較新時才覆蓋（擋亂序節流請求）。

合併邏輯放 Python 純函式（read-modify-write），SQL 端維持跟 user_settings
一樣無聊的 insert...on conflict do update，不引入 jsonb 原子運算——見
tasks/todo.md 的取捨說明。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from app.schemas import LastPlayedInput, ListenMinutesDelta, LookupCountDelta, PatchActivityBody
from shared.db.pool import connection
from shared.models import Activity

router = APIRouter(prefix="/activity", tags=["activity"])

_MAX_STREAK_DATES = 365

_SELECT = """
  select streak_dates, listen_minutes, lookup_count, listened_episode_ids,
         last_played_episode_id, last_played_position,
         to_char(last_played_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as last_played_at
  from public.user_activity where user_id = %s
"""


def _row_to_activity(row: dict[str, Any] | None) -> Activity:
    if row is None:
        return Activity()  # 預設值對齊前端 localStorage 空狀態
    return Activity.model_validate(row)


def _merge_streak_dates(existing: list[str], new_date: str | None) -> list[str]:
    """去重 + 排序 + 上限 365 筆（保留最新）。"""
    dates = set(existing)
    if new_date:
        dates.add(new_date)
    return sorted(dates)[-_MAX_STREAK_DATES:]


def _merge_ids(existing: list[str], new_id: str | None) -> list[str]:
    """去重 append（維持既有順序，新 id 附加在後）。"""
    if new_id is None or new_id in existing:
        return list(existing)
    return [*existing, new_id]


def _merge_counter(
    existing: dict[str, int],
    delta: ListenMinutesDelta | LookupCountDelta | None,
    amount: int | None,
) -> dict[str, int]:
    """指定月份 key 做 int 相加（遞增，非覆蓋）。"""
    if delta is None or amount is None:
        return dict(existing)
    merged = dict(existing)
    merged[delta.month] = merged.get(delta.month, 0) + amount
    return merged


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _merge_last_played(
    existing_episode_id: str | None,
    existing_position: float | None,
    existing_at: str | None,
    new: LastPlayedInput | None,
) -> tuple[str | None, float | None, str | None]:
    """只在新時間戳記較新（或現存為 None）時才覆蓋，擋亂序節流請求蓋掉新進度。"""
    if new is None:
        return existing_episode_id, existing_position, existing_at
    if existing_at is not None:
        try:
            if _parse_ts(new.at) < _parse_ts(existing_at):
                return existing_episode_id, existing_position, existing_at
        except ValueError:
            pass  # 現存時間戳記格式異常：視為不可信，讓新值覆蓋
    return new.episode_id, new.position, new.at


@router.get("", response_model=ApiResponse[Activity])
async def get_activity_ep(user_id: str = Depends(get_current_user)) -> ApiResponse[Activity]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SELECT, (user_id,))
        row = await cur.fetchone()
    return ok(_row_to_activity(row))


@router.patch("", response_model=ApiResponse[Activity])
async def patch_activity_ep(
    body: PatchActivityBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[Activity]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SELECT, (user_id,))
        current = _row_to_activity(await cur.fetchone())

        streak_dates = _merge_streak_dates(current.streak_dates, body.add_streak_date)
        listened_episode_ids = _merge_ids(
            current.listened_episode_ids, body.add_listened_episode_id
        )
        listen_minutes = _merge_counter(
            current.listen_minutes,
            body.add_listen_minutes,
            body.add_listen_minutes.minutes if body.add_listen_minutes else None,
        )
        lookup_count = _merge_counter(
            current.lookup_count,
            body.add_lookup_count,
            body.add_lookup_count.count if body.add_lookup_count else None,
        )
        last_played_episode_id, last_played_position, last_played_at = _merge_last_played(
            current.last_played_episode_id,
            current.last_played_position,
            current.last_played_at,
            body.last_played,
        )

        insert_values = (
            json.dumps(streak_dates),
            json.dumps(listen_minutes),
            json.dumps(lookup_count),
            json.dumps(listened_episode_ids),
            last_played_episode_id,
            last_played_position,
            last_played_at,
        )
        await cur.execute(
            """
            insert into public.user_activity
              (user_id, streak_dates, listen_minutes, lookup_count, listened_episode_ids,
               last_played_episode_id, last_played_position, last_played_at)
            values (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
            on conflict (user_id) do update set
              streak_dates           = %s::jsonb,
              listen_minutes         = %s::jsonb,
              lookup_count           = %s::jsonb,
              listened_episode_ids   = %s::jsonb,
              last_played_episode_id = %s,
              last_played_position   = %s,
              last_played_at         = %s,
              updated_at             = now()
            """,
            (user_id, *insert_values, *insert_values),
        )
        await conn.commit()

    return ok(
        Activity(
            streak_dates=streak_dates,
            listen_minutes=listen_minutes,
            lookup_count=lookup_count,
            listened_episode_ids=listened_episode_ids,
            last_played_episode_id=last_played_episode_id,
            last_played_position=last_played_position,
            last_played_at=last_played_at,
        )
    )
