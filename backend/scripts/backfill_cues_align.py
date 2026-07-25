"""一次性 backfill：對 prod 既有集 cues 等比縮放對齊 mp3 物理時長。

歷史背景：commit a760396 之前生成的 26 集，cues 用 sum(seg.duration + pauses) 累加
（libmp3lame 重編碼每段 +~50ms overhead 未計入），會比實際 mp3 物理時長多 1-2 秒。
本次腳本把每集 cues 等比縮放讓 cues[-1].end 對齊 R2 mp3 物理時長，修源頭而非
加 magic offset。

執行：
    # 只看不寫（推薦先跑）
    uv run python -m scripts.backfill_cues_align --dry-run

    # 實跑（每集獨立 transaction）
    uv run python -m scripts.backfill_cues_align

    # 限前 N 集（驗證 SQL 沒爆）
    uv run python -m scripts.backfill_cues_align --limit 5 --dry-run

從 .env 讀 DATABASE_URL（直連 prod）和 R2 憑證（用 worker-gir 同組）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config
from dotenv import load_dotenv
import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

_SKIP_THRESHOLD = 0.001  # 0.1% 偏差不動（量測浮點誤差）


@dataclass(frozen=True)
class EpisodeRow:
    id: str
    slug: str
    audio_r2_key: str
    cues: list[dict[str, Any]]


def _ffprobe_duration(mp3: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(mp3),
        ],
        text=True,
    ).strip()
    return float(out)


def _scale_cues(cues: list[dict[str, Any]], factor: float) -> list[dict[str, Any]]:
    """等比縮放每個 cue 的 start/end（不可變，回傳新 list）。"""
    return [
        {
            **cue,
            "start": round(float(cue["start"]) * factor, 3),
            "end": round(float(cue["end"]) * factor, 3),
        }
        for cue in cues
    ]


def _make_s3_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _connect_db(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


def _fetch_episodes(cur: psycopg.Cursor, limit: int | None) -> list[EpisodeRow]:
    sql = """
        SELECT id, slug, audio_r2_key, script_json->'cues' AS cues
        FROM episodes
        WHERE audio_r2_key IS NOT NULL
          AND script_json ? 'cues'
          AND jsonb_array_length(script_json->'cues') > 0
        ORDER BY created_at DESC
    """
    if limit:
        sql += " LIMIT %s"
        cur.execute(sql, (limit,))
    else:
        cur.execute(sql)
    return [EpisodeRow(**row) for row in cur.fetchall()]


def _update_episode_cues(conn: psycopg.Connection, episode_id: str, cues: list[dict[str, Any]]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE episodes
            SET script_json = jsonb_set(script_json, '{cues}', %s::jsonb)
            WHERE id = %s
            """,
            (json.dumps(cues), episode_id),
        )


def _process_episode(
    ep: EpisodeRow, s3: Any, *, dry_run: bool
) -> tuple[str, float, float, float] | None:
    """回傳 (slug, factor, last_end_old, last_end_new)；跳過時回 None。"""
    if not ep.cues:
        logger.warning("skip %s: no cues", ep.slug)
        return None
    last_end_old = float(ep.cues[-1]["end"])
    if last_end_old <= 0:
        logger.warning("skip %s: last cue end <= 0", ep.slug)
        return None

    with tempfile.TemporaryDirectory() as tmp:
        mp3_path = Path(tmp) / "episode.mp3"
        try:
            s3.download_file("dawncast", ep.audio_r2_key, str(mp3_path))
        except Exception as exc:
            logger.error("skip %s: R2 download failed: %s", ep.slug, exc)
            return None
        actual = _ffprobe_duration(mp3_path)

    factor = actual / last_end_old
    if abs(factor - 1.0) < _SKIP_THRESHOLD:
        logger.info("skip %s: factor=%.6f in ±0.1%% (actual=%.3f, last_end=%.3f)", ep.slug, factor, actual, last_end_old)
        return None

    last_end_new = round(last_end_old * factor, 3)
    if not dry_run:
        conn = _connect_db(os.environ["DATABASE_URL"])
        try:
            with conn.transaction():
                _update_episode_cues(conn, ep.id, _scale_cues(ep.cues, factor))
        except Exception as exc:
            logger.error("UPDATE failed for %s: %s", ep.slug, exc)
            return None
        finally:
            conn.close()
    logger.info(
        "%s %s factor=%.6f last_end %.3f → %.3f (actual=%.3f)",
        "DRY" if dry_run else "OK ",
        ep.slug, factor, last_end_old, last_end_new, actual,
    )
    return (ep.slug, factor, last_end_old, last_end_new)


def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只看不寫")
    parser.add_argument("--limit", type=int, default=None, help="最多處理幾集")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    s3 = _make_s3_client()
    conn = _connect_db(os.environ["DATABASE_URL"])
    try:
        with conn.cursor() as cur:
            episodes = _fetch_episodes(cur, args.limit)
        logger.info("fetched %d episodes from prod DB", len(episodes))
    finally:
        conn.close()

    stats = {"ok": 0, "skip": 0, "err": 0}
    factors: list[float] = []
    for ep in episodes:
        try:
            result = _process_episode(ep, s3, dry_run=args.dry_run)
        except Exception as exc:
            logger.error("unexpected error for %s: %s", ep.slug, exc)
            stats["err"] += 1
            continue
        if result is None:
            stats["skip"] += 1
        else:
            stats["ok"] += 1
            factors.append(result[1])

    print(f"\n=== summary ({'DRY-RUN' if args.dry_run else 'COMMITTED'}) ===")
    print(f"ok:   {stats['ok']}")
    print(f"skip: {stats['skip']}")
    print(f"err:  {stats['err']}")
    if factors:
        print(f"factors: min={min(factors):.6f} max={max(factors):.6f} mean={sum(factors)/len(factors):.6f}")


if __name__ == "__main__":
    main()
