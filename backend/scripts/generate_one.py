"""跑一筆 generate 並印結果。

執行（須在 backend/ 下，DATABASE_URL=... 從 .env 讀）：
    uv run python -m scripts.generate_one --topic "量子計算"
    uv run python -m scripts.generate_one --topic "區塊鏈基礎" --angle "應用"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date

from psycopg.rows import dict_row

from engine.pipeline.generate_job import run_generate_job
from shared.config import get_settings
from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)


async def _fetch_slug(episode_id: str) -> str:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select slug from public.episodes where id = %s", (episode_id,))
        row = await cur.fetchone()
    if row is None:
        raise RuntimeError(f"找不到 episode {episode_id}")
    return row["slug"]


async def main(topic: str, angle: str, topic_type: str, length_tier: str, user_id: str | None) -> None:
    cfg = get_settings()
    # 沒指定 --user-id 時預設用 DEV_USER_ID：is_free 預設 false，episodes 要有 deliveries
    # 列才會出現在該 user 的首頁；空 user_ids 會生出「誰都看不到」的孤兒集數。
    resolved_uid = user_id or cfg.dev_user_id or None
    body = {
        "big_topic": topic,
        "canonical_topic": topic,
        "angle": angle,
        "topic_type": topic_type,
        "deliver_date": date.today().isoformat(),
        "user_ids": [resolved_uid] if resolved_uid else [],
        "length_tier": length_tier,
    }
    episode_id = await run_generate_job(body, cfg)
    try:
        slug = await _fetch_slug(episode_id)
    finally:
        await close_pool()
    base = cfg.public_base_url.rstrip("/")
    print(f"✓ episode_id={episode_id}  slug={slug}")
    if cfg.local_media_dir:
        print(f"  本地檔: {cfg.local_media_dir}/{slug}.mp4")
    print(f"  影片 URL: {base}/media/{slug}.mp4")
    print(f"  Player:  http://localhost:5173/player/{slug}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="產生一集 podcast 並落庫")
    p.add_argument("--topic", required=True, help="集數主題（會做 slug）")
    p.add_argument("--angle", default="定義", help="切入角度（預設：定義）")
    p.add_argument(
        "--topic-type",
        default="evergreen",
        choices=["news", "product", "evergreen", "skill"],
        help="入口類型：news=今日新聞(GDELT) / product=指定主題(Tavily) / evergreen=深度知識(Wikipedia)（預設：evergreen）",
    )
    p.add_argument(
        "--length-tier",
        default="medium",
        choices=["short", "medium", "long"],
        help="長度 tier（預設：medium）",
    )
    p.add_argument(
        "--user-id",
        default=None,
        help="收件 user_id，會 insert deliveries 讓該 user 首頁看得到（預設：.env 的 DEV_USER_ID）",
    )
    args = p.parse_args()
    asyncio.run(main(args.topic, args.angle, args.topic_type, args.length_tier, args.user_id))
