"""集數組裝共用 service：DB row → 對外 Episode（含 segments 批次簽章）。

GET /episodes/{slug} 與 GET /daily-orders/{date}/episode 共用同一份組裝邏輯，
原本 daily_orders router 直接 `from app.routers.episodes import build_episode`
跨 router 互相 import；集中搬到這個共用模組後，兩個 router 都改依賴這裡，
不再互相 import 對方的 router 檔案。

build_episode 為 async：presigned_get_url(s) 底層是同步 boto3 呼叫，包進
asyncio.to_thread 丟到 thread pool 執行，避免佔住 event loop（同一 process
內其他 request 會被這個同步呼叫卡住）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from shared.models import Cue, Episode, Segment, SourceReference
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


async def build_episode(slug: str, row: dict[str, Any]) -> Episode:
    """把 DB row 組成對外 Episode（含 segments 批次簽章）。

    GET /{slug} 與 /daily-orders/{date}/episode 共用同一份組裝邏輯，避免
    兩條路徑各自組 Episode 時漏簽 segments（後者曾經漏掉，導致該路徑進來的
    集數完全播不了但無任何錯誤訊息——segments 空 list 對前端是合法的「舊集
    未 backfill」狀態，不會報錯，只是靜音）。

    async：segments / legacy audioUrl 簽章都是同步 boto3 呼叫，一律包進
    asyncio.to_thread 丟到 thread pool 執行，呼叫端要 await。
    """
    script_j = row.get("script_json")
    cover_icon_val = script_j.get("cover_icon") if isinstance(script_j, dict) else None

    # 簽章 segments：audio_r2_keys 為 jsonb list（per-line mp3 keys）。
    # 對齊 script_json.cues 順序，前端用 index 跟 Cue 對齊。
    audio_keys: list[str] = list(row.get("audio_r2_keys") or [])
    segment_meta = _segment_metadata_from_script(script_j)
    segments: list[Segment] = []
    if audio_keys and len(audio_keys) == len(segment_meta):
        try:
            signed = await asyncio.to_thread(r2.presigned_get_urls, audio_keys)
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
            audio_url = await asyncio.to_thread(r2.presigned_get_url, legacy_key)
        except Exception:
            logger.exception("legacy audio_r2_key 簽章失敗 slug=%s", slug)

    return Episode(
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
