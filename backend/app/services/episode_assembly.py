"""集數組裝共用 service：DB row → 對外 Episode（含 audio_url 簽章）。

GET /episodes/{slug} 與 GET /daily-orders/{date}/episode 共用同一份組裝邏輯，
原本 daily_orders router 直接 `from app.routers.episodes import build_episode`
跨 router 互相 import；集中搬到這個共用模組後，兩個 router 都改依賴這裡，
不再互相 import 對方的 router 檔案。

build_episode 為 async：presigned_get_url 底層是同步 boto3 呼叫，包進
asyncio.to_thread 丟到 thread pool 執行，避免佔住 event loop（同一 process
內其他 request 會被這個同步呼叫卡住）。

Phase 4：segments 欄位永遠回空 list。前端 Phase 3 後只用 audioUrl，segments
只是契約留位避免 OpenAPI 變動（Segment model 在 P4+1 才刪，見 plan）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from shared.models import Cue, Episode, SourceReference
from shared.storage import r2

logger = logging.getLogger(__name__)

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


async def build_episode(slug: str, row: dict[str, Any]) -> Episode:
    """把 DB row 組成對外 Episode（Phase 4：segments 永遠空 list，audio_url 必簽）。

    GET /{slug} 與 /daily-orders/{date}/episode 共用同一份組裝邏輯。

    async：audioUrl 簽章是同步 boto3 呼叫，包進 asyncio.to_thread 丟到 thread
    pool 執行，呼叫端要 await。
    """
    script_j = row.get("script_json")
    cover_icon_val = script_j.get("cover_icon") if isinstance(script_j, dict) else None

    # audio_r2_key 復用：非 segments 路徑（整集 mp3，含新集雙寫產物與 Gen-1
    # 舊集既有整集檔）才簽 audioUrl。"/segments/" 出現在 key 裡表示這個欄位
    # 還殘留舊版「寫 audio_keys[0]」的污染值（見 reuse_repo.update_episode_keys
    # docstring），不能拿去簽——那把某一行 segment 誤當整集音檔回給前端。
    audio_url: str | None = None
    audio_key = row.get("audio_r2_key")
    if audio_key and "/segments/" not in audio_key:
        try:
            audio_url = await asyncio.to_thread(r2.presigned_get_url, audio_key)
        except Exception:
            logger.exception("audio_r2_key 簽章失敗 slug=%s", slug)

    return Episode(
        id=row["slug"],
        title=row["title"],
        title_zh=row["title_zh"],
        topic=row["topic"],
        cefr_level=row["cefr_level"],
        cover_icon=cover_icon_val,
        is_free=row["is_free"],
        audio_url=audio_url,
        segments=[],  # Phase 4：停產；前端已切換至 audioUrl + Cue.words
        cues=_cues(script_j),
        references=_references_from_sources(row.get("sources")),
    )
