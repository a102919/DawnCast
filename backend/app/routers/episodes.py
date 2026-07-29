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
from shared.models import Episode, EpisodeListItem, RecommendedEpisode

router = APIRouter(prefix="/episodes", tags=["episodes"])

# 列表欄位：對齊前端 MockEpisode。title_zh / episode / published_at 在 DB 可為 NULL，
# 前端 zod 要求非空，故一律 coalesce 出預設值（slug / title / topic / cefr 必有值）。
_LIST_META = """
  e.slug as id,
  e.title,
  coalesce(e.title_zh, '') as title_zh,
  e.topic,
  e.cefr_level,
  e.is_free,
  e.is_featured,
  coalesce(e.episode_no, 0) as episode,
  coalesce(to_char(e.published_at, 'YYYY-MM-DD'), '') as published_at,
  e.script_json->>'cover_icon' as cover_icon
"""


@router.get("", response_model=ApiResponse[list[EpisodeListItem]])
async def list_episodes(
    channel: str | None = None,
    user_id: str = Depends(get_current_user),
) -> ApiResponse[list[EpisodeListItem]]:
    """免費集，或該 user 有 delivery 授權的集數。

    channel：可選頻道 slug，帶了就只回該頻道底下的集數（供 /channels/:slug 詳情頁用）；
    不帶維持既有行為（全站免費／已授權集數）不變。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_LIST_META}
            from public.episodes e
            left join public.channels c on c.id = e.channel_id
            where (%(channel)s::text is null or c.slug = %(channel)s)
              and (e.is_free = true
               or exists (
                 select 1 from public.deliveries d
                 where d.episode_id = e.id and d.user_id = %(user_id)s
               ))
            order by e.published_at desc nulls last, e.created_at desc
            """,
            {"channel": channel, "user_id": user_id},
        )
        rows = await cur.fetchall()
    return ok([EpisodeListItem.model_validate(r) for r in rows])


# 注意：/recommended 必須註冊在 /{slug} 之前，否則會被 {slug}="recommended" 吃掉。
@router.get("/recommended", response_model=ApiResponse[list[RecommendedEpisode]])
async def list_recommended_episodes(
    user_id: str = Depends(get_current_user),
) -> ApiResponse[list[RecommendedEpisode]]:
    """追蹤頻道裡「還沒聽完」的最新集數。跟 Apple Podcasts／Spotify「關注節目的新集數」
    同一種推薦邏輯——不做機器學習或協同過濾，沒人要求，也沒資料量支撐。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"""
            select {_LIST_META}, c.slug as channel_slug, c.name as channel_name
            from public.episodes e
            join public.user_channel_subscriptions s
              on s.channel_id = e.channel_id and s.user_id = %(user_id)s
            join public.channels c on c.id = e.channel_id
            where (e.is_free = true
               or exists (
                 select 1 from public.deliveries d
                 where d.episode_id = e.id and d.user_id = %(user_id)s
               ))
              and not exists (
                select 1 from public.user_activity ua
                where ua.user_id = %(user_id)s
                  and ua.listened_episode_ids @> to_jsonb(e.id::text)
              )
            order by e.published_at desc nulls last, e.created_at desc
            limit 20
            """,
            {"user_id": user_id},
        )
        rows = await cur.fetchall()
    return ok([RecommendedEpisode.model_validate(r) for r in rows])


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


@router.post("/{slug}/play", response_model=ApiResponse[None])
async def record_episode_play(
    slug: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    """播放次數 +1。不去重——使用者重播就是重播，這正是「次數」的語意。

    先過 _fetch_authorized 同一套授權檢查，未授權集數不得灌水播放數。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await _fetch_authorized(cur, slug, user_id)
        await cur.execute(
            "update public.episodes set play_count = play_count + 1 where slug = %s returning slug",
            (slug,),
        )
        updated = await cur.fetchone()
        if updated is None:
            raise NotFoundError("找不到集數")
        await conn.commit()
    return ok(None)


# Phase G：移除 GET /{slug}/url 端點。前端吃 Episode.segments[] 陣列，
# 不再需要整集 mp3 簽章 URL；改 audioUrl 欄位於 Episode 永遠 None。
# 若之後真的需要回舊行為，重新開路由並從 episodes.audio_r2_key[0] 簽即可。
