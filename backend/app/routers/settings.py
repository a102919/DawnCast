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

# cefr_level 存 users.cefr_target（生成引擎讀同一欄），其餘存 user_settings。
# 從 users 出發 left join：user_settings 無列時 settings 欄位全 NULL，
# 由 _row_to_settings 丟掉 None 讓 model 預設值補——「無列」不再是特殊情況。
_SELECT = """
  select s.popup_enabled, s.popup_dont_show_again, s.playback_rate,
         s.font_size, s.theme, s.preferred_topics,
         to_char(s.default_delivery_time, 'HH24:MI') as default_delivery_time,
         u.cefr_target as cefr_level
  from public.users u
  left join public.user_settings s on s.user_id = u.id
  where u.id = %s
"""


def _row_to_settings(row: dict[str, Any] | None) -> Settings:
    if row is None:
        return Settings()  # 預設值對齊前端 DEFAULT_SETTINGS
    return Settings.model_validate({k: v for k, v in row.items() if v is not None})


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
        if body.cefr_level is not None:
            await cur.execute(
                "update public.users set cefr_target = %s where id = %s",
                (body.cefr_level, user_id),
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
