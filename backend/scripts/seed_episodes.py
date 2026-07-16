"""把既有內容灌進 episodes 表，方便前端與測試。

來源（皆為唯讀，不改前端檔）：
  - frontend/src/routes/episodeData.ts 的 EPISODES 陣列 → 元資訊（slug/title/topic/...）。
  - frontend/public/data/episode.json → 對應 slug 的 cues 內容（若存在）。

用 upsert by slug（ON CONFLICT (slug) DO UPDATE），重跑冪等。
執行：DATABASE_URL=... uv run python -m scripts.seed_episodes
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)

# backend/ → 專案根 → frontend/
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = _BACKEND_ROOT.parent
_EPISODE_DATA_TS = _PROJECT_ROOT / "frontend" / "src" / "routes" / "episodeData.ts"
_EPISODE_JSON = _PROJECT_ROOT / "frontend" / "public" / "data" / "episode.json"


def _parse_episode_data_ts(text: str) -> list[dict[str, Any]]:
    """從 episodeData.ts 抽出 EPISODES 陣列。

    TS 物件不是合法 JSON（key 無引號、有尾逗號、用單引號），用正則逐物件擷取欄位。
    只取 seed 需要的欄位；缺欄位用合理預設。刻意不引第三方 TS parser（無強理由勿加依賴）。
    """
    start = text.find("EPISODES")
    block_start = text.find("[", start)
    block_end = text.find("] as const", block_start)
    if block_start == -1 or block_end == -1:
        raise ValueError("episodeData.ts 找不到 EPISODES 陣列")
    block = text[block_start:block_end]

    episodes: list[dict[str, Any]] = []
    for obj in re.findall(r"\{([^}]*)\}", block):
        fields = dict(re.findall(r"(\w+)\s*:\s*'([^']*)'", obj))
        bools = dict(re.findall(r"(\w+)\s*:\s*(true|false)\b", obj))
        nums = dict(re.findall(r"(\w+)\s*:\s*(\d+)\b", obj))
        slug = fields.get("id")
        if not slug:
            continue
        episodes.append(
            {
                "slug": slug,
                "title": fields.get("title", slug),
                "title_zh": fields.get("titleZh"),
                "topic": fields.get("topic", "tech"),
                "cefr_level": fields.get("cefrLevel", "B1"),
                "is_free": bools.get("isFree") == "true",
                "is_featured": bools.get("isFeatured") == "true",
                "episode_no": int(nums["episode"]) if "episode" in nums else None,
                "published_at": fields.get("publishedAt"),
            }
        )
    return episodes


def _load_cues_by_slug() -> dict[str, list[dict[str, Any]]]:
    """讀 episode.json，回傳 {slug: cues}。檔案不存在則回空 dict（不致命）。"""
    if not _EPISODE_JSON.exists():
        logger.warning("episode.json 不存在，集數將無 cues 內容：%s", _EPISODE_JSON)
        return {}
    data = json.loads(_EPISODE_JSON.read_text(encoding="utf-8"))
    slug = data.get("id")
    cues = data.get("cues")
    if not slug or not isinstance(cues, list):
        return {}
    return {slug: cues}


async def _upsert_episode(meta: dict[str, Any], cues: list[dict[str, Any]] | None) -> None:
    """以 slug 為鍵 upsert 一集。有 cues 才寫 script_json（避免覆蓋成 null）。"""
    script_json = json.dumps({"cues": cues}, ensure_ascii=False) if cues else None
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.episodes
                (slug, title, title_zh, topic, cefr_level, is_free,
                 is_featured, episode_no, published_at, script_json)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            on conflict (slug) do update set
                title = excluded.title,
                title_zh = excluded.title_zh,
                topic = excluded.topic,
                cefr_level = excluded.cefr_level,
                is_free = excluded.is_free,
                is_featured = excluded.is_featured,
                episode_no = excluded.episode_no,
                published_at = excluded.published_at,
                script_json = coalesce(excluded.script_json, public.episodes.script_json)
            """,
            (
                meta["slug"],
                meta["title"],
                meta["title_zh"],
                meta["topic"],
                meta["cefr_level"],
                meta["is_free"],
                meta["is_featured"],
                meta["episode_no"],
                meta["published_at"],
                script_json,
            ),
        )


async def seed() -> int:
    """執行 seed，回傳 upsert 的集數。"""
    metas = _parse_episode_data_ts(_EPISODE_DATA_TS.read_text(encoding="utf-8"))
    cues_by_slug = _load_cues_by_slug()
    for meta in metas:
        await _upsert_episode(meta, cues_by_slug.get(meta["slug"]))
    logger.info("seed 完成：upsert %d 集（其中 %d 集帶 cues）", len(metas), len(cues_by_slug))
    return len(metas)


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        await seed()
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_amain())
