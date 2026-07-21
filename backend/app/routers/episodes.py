"""集數 router：list / get(slug) / 簽章 URL。

授權：免費集（is_free）或該 user 有 delivery 授權才可取內容 / URL。
對外用 slug 當 id。cues 從 episodes.script_json 取。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from shared.config import get_settings
from shared.db.pool import connection
from shared.errors import ForbiddenError, NotFoundError
from shared.models import Cue, Episode, EpisodeListItem
from shared.storage import r2

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/episodes", tags=["episodes"])

# 列表欄位：對齊前端 MockEpisode。title_zh / episode / published_at 在 DB 可為 NULL，
# 前端 zod 要求非空，故一律 coalesce 出預設值（slug / title / topic / cefr 必有值）。
_LIST_META = """
  slug as id,
  title,
  coalesce(title_zh, '') as title_zh,
  topic,
  cefr_level,
  is_free,
  is_featured,
  coalesce(episode_no, 0) as episode,
  coalesce(to_char(published_at, 'YYYY-MM-DD'), '') as published_at
"""


def _cues(script_json: Any) -> list[Cue]:
    """script_json 可能是 {cues:[...]} 或直接 [...]，皆容錯。"""
    if not script_json:
        return []
    raw = script_json.get("cues") if isinstance(script_json, dict) else script_json
    if not isinstance(raw, list):
        return []
    return [Cue.model_validate(c) for c in raw]


@router.get("", response_model=ApiResponse[list[EpisodeListItem]])
async def list_episodes(
    user_id: str = Depends(get_current_user),
) -> ApiResponse[list[EpisodeListItem]]:
    """免費集，或該 user 有 delivery 授權的集數。"""
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_LIST_META}
            from public.episodes e
            where e.is_free = true
               or exists (
                 select 1 from public.deliveries d
                 where d.episode_id = e.id and d.user_id = %s
               )
            order by e.published_at desc nulls last, e.created_at desc
            """,
            (user_id,),
        )
        rows = await cur.fetchall()
    return ok([EpisodeListItem.model_validate(r) for r in rows])


async def _fetch_authorized(cur: Any, slug: str, user_id: str) -> dict[str, Any]:
    """取集數列並驗授權；無權 raise ForbiddenError，不存在 raise NotFoundError。"""
    await cur.execute(
        """
        select e.id, e.slug, e.title, e.title_zh, e.topic, e.cefr_level,
               e.is_free, e.script_json, e.audio_r2_key,
               exists (
                 select 1 from public.deliveries d
                 where d.episode_id = e.id and d.user_id = %s
               ) as has_delivery
        from public.episodes e where e.slug = %s
        """,
        (user_id, slug),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFoundError("找不到集數")
    if not row["is_free"] and not row["has_delivery"]:
        raise ForbiddenError("無此集數權限")
    return dict(row)


@router.get("/{slug}", response_model=ApiResponse[Episode])
async def get_episode(slug: str, user_id: str = Depends(get_current_user)) -> ApiResponse[Episode]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        row = await _fetch_authorized(cur, slug, user_id)
    episode = Episode(
        id=row["slug"],
        title=row["title"],
        title_zh=row["title_zh"],
        topic=row["topic"],
        cefr_level=row["cefr_level"],
        is_free=row["is_free"],
        cues=_cues(row["script_json"]),
    )
    return ok(episode)


@router.get("/{slug}/url", response_model=ApiResponse[str])
async def get_episode_url(slug: str, user_id: str = Depends(get_current_user)) -> ApiResponse[str]:
    """產 R2 簽章 URL。先驗授權（免費或有 delivery），通過才 presign。

    本機 fallback：當 R2 key 為 NULL 且 LOCAL_MEDIA_DIR 設定時，回 /media/{slug}.mp3
    （router 只關 mp3，不再產 mp4）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        row = await _fetch_authorized(cur, slug, user_id)
    key = row["audio_r2_key"]
    if not key:
        settings = get_settings()
        media_dir = settings.local_media_dir
        if media_dir:
            mp3_local = Path(media_dir) / f"{slug}.mp3"
            if mp3_local.is_file():
                # ponytail: 回相對路徑讓 vite proxy / 同源 origin 處理，
                # 不寫死 host（devtunnel 是 HTTPS，localhost:8000 會被瀏覽器擋）。
                return ok(f"/media/{slug}.mp3")
            # ponytail: 半完成 record 偵測——有 .mp4 但無 .mp3 多半是早期 pipeline stub，
            # 留著會 silent fail（player 整頁炸、log 無線索），主動 log + 明確錯誤訊息。
            mp4_local = Path(media_dir) / f"{slug}.mp4"
            if mp4_local.is_file():
                logger.warning(
                    "半完成 episode：%s 有 .mp4 stub 但無 .mp3 / R2 key（size=%d bytes）",
                    slug, mp4_local.stat().st_size,
                )
                raise NotFoundError("此集數媒體尚未完成轉檔（找到 .mp4 stub）")
        raise NotFoundError("此集數尚無媒體檔")
    return ok(r2.presigned_get_url(key))
