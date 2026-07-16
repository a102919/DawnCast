"""設定 router：getSettings / updateSettings / resetPopupPreferences。

無列回預設（trigger 通常已補列，仍防呆）。upsert 只動有給的欄位。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from app.schemas import UpdateSettingsBody
from shared.db.pool import connection
from shared.models import Settings

router = APIRouter(prefix="/settings", tags=["settings"])

_SELECT = """
  select popup_enabled, popup_dont_show_again, playback_rate,
         font_size, theme, preferred_topics,
         to_char(default_delivery_time, 'HH24:MI') as default_delivery_time
  from public.user_settings where user_id = %s
"""


def _row_to_settings(row: dict[str, Any] | None) -> Settings:
    if row is None:
        return Settings()  # 預設值對齊前端 DEFAULT_SETTINGS
    return Settings.model_validate(row)


@router.get("", response_model=ApiResponse[Settings])
async def get_settings_ep(user_id: str = Depends(get_current_user)) -> ApiResponse[Settings]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SELECT, (user_id,))
        row = await cur.fetchone()
    return ok(_row_to_settings(row))


@router.patch("", response_model=ApiResponse[Settings])
async def update_settings_ep(
    body: UpdateSettingsBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[Settings]:
    """upsert：以預設列為底，coalesce 套用 patch 有給的欄位。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        import json

        topics_json = None if body.preferred_topics is None else json.dumps(body.preferred_topics)
        await cur.execute(
            """
            insert into public.user_settings
              (user_id, popup_enabled, popup_dont_show_again, playback_rate,
               font_size, theme, preferred_topics, default_delivery_time)
            values (%s,
                    coalesce(%s, true), coalesce(%s, false), coalesce(%s, 1),
                    coalesce(%s, 'md'), coalesce(%s, 'auto'),
                    coalesce(%s::jsonb, '[]'::jsonb), coalesce(%s, '07:00'))
            on conflict (user_id) do update set
              popup_enabled         = coalesce(%s, user_settings.popup_enabled),
              popup_dont_show_again = coalesce(%s, user_settings.popup_dont_show_again),
              playback_rate         = coalesce(%s, user_settings.playback_rate),
              font_size             = coalesce(%s, user_settings.font_size),
              theme                 = coalesce(%s, user_settings.theme),
              preferred_topics      = coalesce(%s::jsonb, user_settings.preferred_topics),
              default_delivery_time = coalesce(%s, user_settings.default_delivery_time),
              updated_at            = now()
            """,
            (
                user_id,
                body.popup_enabled,
                body.popup_dont_show_again,
                body.playback_rate,
                body.font_size,
                body.theme,
                topics_json,
                body.default_delivery_time,
                body.popup_enabled,
                body.popup_dont_show_again,
                body.playback_rate,
                body.font_size,
                body.theme,
                topics_json,
                body.default_delivery_time,
            ),
        )
        await cur.execute(_SELECT, (user_id,))
        row = await cur.fetchone()
        await conn.commit()
    return ok(_row_to_settings(row))


@router.post("/reset-popup", response_model=ApiResponse[None])
async def reset_popup_preferences(
    user_id: str = Depends(get_current_user),
) -> ApiResponse[None]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            insert into public.user_settings (user_id, popup_enabled, popup_dont_show_again)
            values (%s, true, false)
            on conflict (user_id) do update set
              popup_enabled = true, popup_dont_show_again = false, updated_at = now()
            """,
            (user_id,),
        )
        await conn.commit()
    return ok(None)
