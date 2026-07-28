"""集數 router：list / get(slug)。

授權：免費集（is_free）或該 user 有 delivery 授權才可取內容。
對外用 slug 當 id。cues 從 episodes.script_json 取。segments 從
episodes.audio_r2_keys jsonb 取，r2.presigned_get_urls 一次簽 N 個。

新方案下整集 mp3 不再生產，audioUrl 對 episode 永遠 None；
舊 GET /{slug}/url 端點已於 Phase G 移除（前端全部改吃 segments[]）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from app.services.episode_assembly import build_episode
from shared.db.pool import connection
from shared.errors import ForbiddenError, NotFoundError
from shared.models import Episode, EpisodeListItem

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
  coalesce(to_char(published_at, 'YYYY-MM-DD'), '') as published_at,
  script_json->>'cover_icon' as cover_icon
"""


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
               e.is_free, e.script_json, e.audio_r2_key, e.audio_r2_keys,
               e.sources,
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
    return ok(await build_episode(slug, row))


# Phase G：移除 GET /{slug}/url 端點。前端吃 Episode.segments[] 陣列，
# 不再需要整集 mp3 簽章 URL；改 audioUrl 欄位於 Episode 永遠 None。
# 若之後真的需要回舊行為，重新開路由並從 episodes.audio_r2_key[0] 簽即可。
