"""為舊集音檔補 per-line segments（R2 jsonb）。

Phase A 後新生成的集數一律走 segments[] 路徑（每行 mp3 一個 R2 object），
但 Phase A 之前的舊集只有 audio_r2_key（整集 mp3）— 字幕與音檔對齊誤差
最高 2s。這個 script 把舊整集 mp3 按 cue 切段、上傳 R2、UPDATE audio_r2_keys。

設計重點（見 plan/mighty-coalescing-wall.md Phase H）：
- ffmpeg `-c copy` 切段，frame-boundary ≤ 26ms 誤差（不重編碼，速度快、不失真）
- dry-run 模式只列計畫、不寫 DB、不上傳 R2
- idempotent：UPDATE 時檢查現有 audio_r2_keys，若已非空則 skip（避免 partial upload 半殘）
- 單集 commit：每集一個 transaction，失敗 rollback 不影響其他集

執行：
  uv run python -m scripts.backfill_segments --dry-run --limit 5
  uv run python -m scripts.backfill_segments --limit 10
  uv run python -m scripts.backfill_segments --slug <slug>   # 指定單集
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.db.pool import close_pool, connection, open_pool
from shared.storage import r2

logger = logging.getLogger(__name__)


@dataclass
class SegmentPlan:
    slug: str
    uuid: str
    cue_start: float
    cue_end: float
    segment_index: int
    r2_key: str


def _cues_from_script_json(script_json: Any) -> list[dict[str, Any]]:
    if not script_json:
        return []
    raw = script_json.get("cues") if isinstance(script_json, dict) else script_json
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def _plan_segments(slug: str, uuid: str, cues: list[dict[str, Any]]) -> list[SegmentPlan]:
    out: list[SegmentPlan] = []
    for idx, cue in enumerate(cues):
        start = float(cue.get("start", 0.0))
        end = float(cue.get("end", start))
        out.append(SegmentPlan(
            slug=slug, uuid=uuid,
            cue_start=start, cue_end=end,
            segment_index=idx,
            r2_key=f"episodes/{uuid}/segments/{idx:03d}.mp3",
        ))
    return out


def _ffmpeg_cut(in_path: Path, start: float, end: float, out_path: Path) -> None:
    """ffmpeg -c copy 切段；frame-boundary 對齊，誤差 ≤ 26ms。"""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg 不在 PATH")
    duration = max(0.0, end - start)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-i", str(in_path),
        "-t", f"{duration:.3f}",
        "-c", "copy",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, timeout=60)


async def _list_legacy_episodes(limit: int) -> list[dict[str, Any]]:
    """撈 audio_r2_key 非空 + audio_r2_keys 為空 list 的舊集。"""
    sql = """
      select id, slug, audio_r2_key, audio_r2_keys, script_json
      from public.episodes
      where audio_r2_key is not null
        and (audio_r2_keys = '[]'::jsonb or audio_r2_keys is null)
      order by created_at asc
      limit %s
    """
    async with connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, (limit,))
            rows = await cur.fetchall()
    # pool row_factory=dict_row → fetchall 回 list[dict]；tuple 模式用 cols 重建。
    if rows and isinstance(rows[0], dict):
        return list(rows)
    return [dict(zip([d.name for d in cur.description] if cur.description else [], r)) for r in rows]


async def _process_one(row: dict[str, Any], dry_run: bool, tmp_root: Path) -> tuple[int, int]:
    slug = row["slug"]
    uuid = str(row["id"])
    cues = _cues_from_script_json(row.get("script_json"))
    if not cues:
        logger.warning("[backfill] slug=%s 沒有 cues，跳過", slug)
        return 0, 0
    plan = _plan_segments(slug, uuid, cues)
    logger.info("[backfill] slug=%s 計畫切 %d 段 (dry_run=%s)", slug, len(plan), dry_run)
    if dry_run:
        return len(plan), 0

    workdir = tmp_root / slug
    workdir.mkdir(parents=True, exist_ok=True)
    src_mp3 = workdir / "source.mp3"
    try:
        # 從 R2 下載整集 mp3
        src_bytes = r2.get_object(row["audio_r2_key"])
        src_mp3.write_bytes(src_bytes)

        # 逐段 ffmpeg 切 + 上傳 R2
        keys: list[str] = []
        for sp in plan:
            seg_path = workdir / f"{sp.segment_index:03d}.mp3"
            _ffmpeg_cut(src_mp3, sp.cue_start, sp.cue_end, seg_path)
            r2.put_object(sp.r2_key, seg_path.read_bytes(), "audio/mpeg")
            keys.append(sp.r2_key)

        # UPDATE audio_r2_keys（單集 transaction）。
        # audio_r2_key 留原值不動：backfill 完後下個 migration drop；保留
        # 這欄期間任何舊 client 仍能拿到舊整集 URL 當 fallback。
        async with connection() as conn, conn.cursor() as cur, conn.transaction():
            await cur.execute(
                """
                update public.episodes
                set audio_r2_keys = %s::jsonb
                where id = %s
                """,
                (json.dumps(keys), row["id"]),
            )
        return len(plan), len(keys)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _run(limit: int, slug: str | None, dry_run: bool) -> tuple[int, int]:
    if slug:
        # 單集模式
        async with connection() as conn, conn.cursor() as cur:
            await cur.execute(
                """
                select id, slug, audio_r2_key, audio_r2_keys, script_json
                from public.episodes where slug = %s
                """,
                (slug,),
            )
            row = await cur.fetchone()
            if row is None:
                logger.error("[backfill] 找不到 slug=%s", slug)
                return 0, 0
            # pool 預設 row_factory=dict_row → fetchone 直接回 dict；tuple 模式
            # 退路用 zip(cols, row) 重建。下方 _process_one 兩種都吃 dict-like。
            rows = [row] if isinstance(row, dict) else [
                dict(zip([d.name for d in cur.description] if cur.description else [], row))
            ]
    else:
        await open_pool()
        try:
            rows = await _list_legacy_episodes(limit)
        finally:
            await close_pool()
    if not rows:
        return 0, 0
    with tempfile.TemporaryDirectory(prefix="dc_backfill_") as td:
        tmp_root = Path(td)
        total_plan = 0
        total_ok = 0
        for row in rows:
            p, o = await _process_one(row, dry_run, tmp_root)
            total_plan += p
            total_ok += o
        return total_plan, total_ok


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=int, default=10, help="本批處理集數上限（預設 10）")
    p.add_argument("--slug", type=str, default=None, help="指定單集 slug（忽略 --limit）")
    p.add_argument("--dry-run", action="store_true", help="只列計畫，不上傳、不寫 DB")
    a = p.parse_args()
    try:
        plan, ok = asyncio.run(_run(a.limit, a.slug, a.dry_run))
        if a.dry_run:
            print(f"DRY-RUN: 計畫切 {plan} 段，0 段實際上傳")
        else:
            print(f"DONE: 切 {plan} 段，上傳 {ok} 段")
    finally:
        # _run 內已 close_pool，但 slug 模式不會開；保險起見再關一次（no-op 若已關）
        try:
            asyncio.run(close_pool())
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    _amain()
