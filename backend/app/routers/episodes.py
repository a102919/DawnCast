"""集數 router：list / get(slug)。

授權：免費集（is_free）或該 user 有 delivery 授權才可取內容。
對外用 slug 當 id。cues 從 episodes.script_json 取。segments 從
episodes.audio_r2_keys jsonb 取，r2.presigned_get_urls 一次簽 N 個。

新方案下整集 mp3 不再生產，audioUrl 對 episode 永遠 None；
舊 GET /{slug}/url 端點已於 Phase G 移除（前端全部改吃 segments[]）。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.deps import get_current_user
from app.response import ApiResponse, ok
from shared.db.pool import connection
from shared.errors import ForbiddenError, NotFoundError
from shared.models import Cue, Episode, EpisodeListItem, Segment, SourceReference
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
  coalesce(to_char(published_at, 'YYYY-MM-DD'), '') as published_at,
  script_json->>'cover_icon' as cover_icon
"""

# 允許的 URL scheme：只放行 http / https，避免 javascript: / data: / file: 等
# 偽 protocol 進到前端（XSS / SSRF 風險）。scheme 比對前先 strip + lower。
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def _cues(script_json: Any) -> list[Cue]:
    """script_json 可能是 {cues:[...]} 或直接 [...]，皆容錯。"""
    if not script_json:
        return []
    raw = script_json.get("cues") if isinstance(script_json, dict) else script_json
    if not isinstance(raw, list):
        return []
    return [Cue.model_validate(c) for c in raw]


def _safe_url(url: Any) -> str | None:
    """只放行 http(s) 開頭的 URL，回 strip 後的字串；其餘回 None。

    防止 javascript: / data: / file: 等危險 URL 進入前端 <a> 標籤。
    """
    if not url or not isinstance(url, str):
        return None
    cleaned = url.strip()
    try:
        scheme = urlparse(cleaned).scheme.lower()
    except ValueError:
        return None
    if scheme not in _ALLOWED_URL_SCHEMES:
        return None
    return cleaned


def _references_from_sources(sources_json: Any) -> list[SourceReference]:
    """把 DB 落庫的 sources jsonb 轉成對外 SourceReference list。

    只挑 id / title / url 三欄（不暴露 text / published_at），並以 URL scheme
    白名單過濾。落庫殘留的 schema 漂移（缺欄位、非 dict）一律略過該筆，單筆髒
    不污染整集。
    """
    if not sources_json or not isinstance(sources_json, list):
        return []
    out: list[SourceReference] = []
    for entry in sources_json:
        if not isinstance(entry, dict):
            continue
        url = _safe_url(entry.get("url"))
        if url is None:
            continue
        sid = entry.get("id")
        title = entry.get("title")
        if not isinstance(sid, str) or not sid:
            continue
        if not isinstance(title, str):
            title = ""
        out.append(SourceReference(id=sid, title=title, url=url))
    return out


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


def _segment_metadata_from_script(script_json: Any) -> list[dict[str, Any]]:
    """從 script_json.cues 算每行 segment 對齊 metadata（duration, start, end）。

    新方案下每行 mp3 對應一個 segment，duration 從該行 cue 實際時長推得。
    如果 script_json 沒有 cues（舊集沒存），回空 list——此時 segments 留空，
    前端走 audioUrl fallback（雖然 audioUrl 為 null，舊 client 會 graceful 失敗）。
    """
    cues = _cues(script_json)
    return [
        {
            "index": i,
            "duration": max(0.0, cue.end - cue.start),
            "start": cue.start,
            "end": cue.end,
        }
        for i, cue in enumerate(cues)
    ]


@router.get("/{slug}", response_model=ApiResponse[Episode])
async def get_episode(slug: str, user_id: str = Depends(get_current_user)) -> ApiResponse[Episode]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        row = await _fetch_authorized(cur, slug, user_id)
    script_j = row.get("script_json")
    cover_icon_val = (
        script_j.get("cover_icon")
        if isinstance(script_j, dict)
        else None
    )

    # 簽章 segments：audio_r2_keys 為 jsonb list（per-line mp3 keys）。
    # 對齊 script_json.cues 順序，前端用 index 跟 Cue 對齊。
    audio_keys: list[str] = list(row.get("audio_r2_keys") or [])
    segment_meta = _segment_metadata_from_script(script_j)
    segments: list[Segment] = []
    if audio_keys and len(audio_keys) == len(segment_meta):
        try:
            signed = r2.presigned_get_urls(audio_keys)
        except Exception:
            logger.exception("segments 批次簽章失敗 slug=%s", slug)
            signed = {}
        for key, meta in zip(audio_keys, segment_meta, strict=True):
            url = signed.get(key)
            if url is None:
                continue
            segments.append(
                Segment(
                    index=meta["index"],
                    audio_url=url,
                    duration=meta["duration"],
                    start=meta["start"],
                    end=meta["end"],
                )
            )

    # ponytail: 整集 mp3 不再產，audioUrl 永遠 None；保留欄位給向後相容。
    audio_url: str | None = None
    legacy_key = row.get("audio_r2_key")
    if legacy_key and not segments:
        # 舊集未 backfill：用舊 audio_r2_key 簽章回 audioUrl 給仍吃舊路徑的 client。
        # 1 版本後 Phase G 移除。
        try:
            audio_url = r2.presigned_get_url(legacy_key)
        except Exception:
            logger.exception("legacy audio_r2_key 簽章失敗 slug=%s", slug)

    episode = Episode(
        id=row["slug"],
        title=row["title"],
        title_zh=row["title_zh"],
        topic=row["topic"],
        cefr_level=row["cefr_level"],
        cover_icon=cover_icon_val,
        is_free=row["is_free"],
        audio_url=audio_url,
        segments=segments,
        cues=_cues(script_j),
        references=_references_from_sources(row.get("sources")),
    )
    return ok(episode)


# Phase G：移除 GET /{slug}/url 端點。前端吃 Episode.segments[] 陣列，
# 不再需要整集 mp3 簽章 URL；改 audioUrl 欄位於 Episode 永遠 None。
# 若之後真的需要回舊行為，重新開路由並從 episodes.audio_r2_key[0] 簽即可。
