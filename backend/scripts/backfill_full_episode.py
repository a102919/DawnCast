"""一次性 backfill：把 Gen-2 集數（每行一個 mp3 segment）合併成整集 episode.mp3。

背景（見 /Users/alan/.claude/plans/podcast-ios-bug-jiggly-cocoa.md「backfill」節）：
播放器重構把「逐行 mp3 + 前端 A/B 輪替接播」改回「整集單一 mp3 + 單一 <audio>」，
時間軸與音檔由 engine.media.audio.plan_layout/concat_episode 這一份程式碼產生
（drift=0 是數學保證）。這支 script 補齊舊集：

- Gen-1（Phase A 之前的 26+ 集）：audio_r2_key 本來就指向整集 mp3，只需 HEAD
  確認物件還在，不重算——skip。
- Gen-2（Phase A 之後、本次重構之前生成的集數）：audio_r2_key 或空或被舊版
  `update_episode_keys` 誤寫成 audio_keys[0]（污染值，含 "/segments/"），實際
  音檔散在 audio_r2_keys 逐行 segment。下載這些 segment、重跑與新集完全相同的
  plan_layout → concat_episode → build_timeline，換掉 audio_r2_key 與
  script_json.cues 的 start/end；text/zh/speaker/words 從舊 cues 原封搬（單一
  事實來源只在「時間軸怎麼算」，不重寫已核對過的字幕內容）。

不走 pgmq：一次性、集數不多（幾十集），適合人盯著跑。冪等：Gen-1 判斷本身就是
「已完成」訊號，重跑自然 skip；Gen-2 上傳 R2 覆寫同一個 key，DB UPDATE 失敗
不 rollback R2（下次重跑會重新產生同一份 mp3 再試一次 UPDATE）。

執行：
    # 只看不寫（推薦先跑）
    uv run python -m scripts.backfill_full_episode --dry-run --limit 5

    # 抽驗幾集，看 ffprobe 對 cues[-1].end 的差
    uv run python -m scripts.backfill_full_episode --dry-run --limit 3

    # 指定單集
    uv run python -m scripts.backfill_full_episode --slug <slug> --dry-run

    # 實跑（上傳 R2 + 寫 DB）
    uv run python -m scripts.backfill_full_episode --apply --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

from engine.media.audio import concat_episode, plan_layout, probe_stream_info, synthesize_silence
from engine.media.subtitles import build_timeline
from engine.media.tts import SynthSegment, _probe_duration
from shared.config import get_settings
from shared.db.pool import close_pool, connection
from shared.errors import StorageError, TTSError
from shared.models import Cue, WordOffset
from shared.storage import r2

logger = logging.getLogger(__name__)

Classification = Literal["gen1", "gen2"]


@dataclass(frozen=True)
class EpisodeRow:
    id: str
    slug: str
    audio_r2_key: str | None
    audio_r2_keys: list[str]
    script_json: dict[str, Any]


@dataclass(frozen=True)
class PlanResult:
    """Gen-2 一集重新合成的結果；dry-run 與 --apply 共用同一份計算。"""

    slug: str
    segment_count: int
    new_duration: float
    old_last_end: float
    diff: float
    mp3_path: Path
    cues: list[Cue]


@dataclass
class Stats:
    gen1_skip: int = 0
    gen1_missing: list[str] = field(default_factory=list)
    gen2_planned: int = 0
    gen2_applied: int = 0
    gen2_db_failed: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


# ── 分流（純邏輯，無 IO）────────────────────────────────────────


def classify_episode(audio_r2_key: str | None) -> Classification:
    """audio_r2_key 非空且不含 "/segments/" → Gen-1（整集檔本來就在）；否則 Gen-2。

    "否則" 涵蓋兩種情況：欄位是 None/空字串（從沒寫過整集 key），或被舊版
    `update_episode_keys` 誤寫成 audio_keys[0] 的污染值（含 "/segments/"）。
    兩者都要重新合成，不能只看「有沒有值」。
    """
    if audio_r2_key and "/segments/" not in audio_r2_key:
        return "gen1"
    return "gen2"


# ── cues 合併（純邏輯，無 IO）────────────────────────────────────


def _merge_cues(new_cues: Sequence[Cue], old_cues: Sequence[dict[str, Any]]) -> list[Cue]:
    """新 cues 的 start/end + 舊 cues 的 text/zh/speaker/words。

    重新合成只換時間軸——text/zh/speaker 已經人工核對過（或至少跟當初上線的
    腳本一致），沒有理由跟著重寫。行數不一致代表舊資料本身就有問題（segment
    數與 cues 數對不上），交給呼叫端捕捉 ValueError 標記該集錯誤跳過。
    """
    if len(new_cues) != len(old_cues):
        raise ValueError(f"新 cues（{len(new_cues)} 行）與舊 cues（{len(old_cues)} 行）行數不一致")
    merged: list[Cue] = []
    for new_cue, old_cue in zip(new_cues, old_cues, strict=True):
        words_raw = old_cue.get("words")
        words = [WordOffset(**w) for w in words_raw] if words_raw else None
        merged.append(
            Cue(
                index=new_cue.index,
                speaker=old_cue.get("speaker", new_cue.speaker),
                text=old_cue.get("text", new_cue.text),
                zh=old_cue.get("zh", new_cue.zh),
                start=new_cue.start,
                end=new_cue.end,
                words=words,
            )
        )
    return merged


def _script_lines(script_json: dict[str, Any]) -> list[dict[str, Any]]:
    raw = script_json.get("script")
    if not isinstance(raw, list):
        raise KeyError("script_json 缺少 script 欄位")
    return [line for line in raw if isinstance(line, dict)]


def _old_cues(script_json: dict[str, Any]) -> list[dict[str, Any]]:
    raw = script_json.get("cues")
    if not isinstance(raw, list):
        raise KeyError("script_json 缺少 cues 欄位")
    return [c for c in raw if isinstance(c, dict)]


# ── DB ──────────────────────────────────────────────────────────


async def _fetch_episodes(limit: int, slug: str | None) -> list[EpisodeRow]:
    """撈 audio_r2_keys 非空的全部集數（Gen-1 舊集也在裡面——backfill_segments.py
    當年替它們補過 segments，這裡靠 classify_episode 再分流一次）。
    """
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if slug:
            await cur.execute(
                """
                select id, slug, audio_r2_key, audio_r2_keys, script_json
                from public.episodes
                where slug = %s
                """,
                (slug,),
            )
        else:
            await cur.execute(
                """
                select id, slug, audio_r2_key, audio_r2_keys, script_json
                from public.episodes
                where audio_r2_keys is not null
                  and jsonb_array_length(audio_r2_keys) > 0
                order by created_at asc
                limit %s
                """,
                (limit,),
            )
        rows = await cur.fetchall()
    return [
        EpisodeRow(
            id=str(row["id"]),
            slug=row["slug"],
            audio_r2_key=row["audio_r2_key"],
            audio_r2_keys=list(row["audio_r2_keys"] or []),
            script_json=row["script_json"] or {},
        )
        for row in rows
    ]


async def _update_episode(episode_id: str, audio_key: str, cues: list[Cue]) -> None:
    """單一 UPDATE：只換 audio_r2_key 與 script_json.cues，其餘欄位原封不動。

    刻意不呼叫 reuse_repo.update_episode_keys：那支函式的 UPDATE 對
    extracted_facts/target_vocab/srt_r2_key 沒有 coalesce（設計給 render
    pipeline 一次寫滿全部欄位），backfill 只想換兩個值，直接借用會把這三欄
    清空。
    """
    cues_json = json.dumps([c.model_dump(by_alias=False) for c in cues], ensure_ascii=False)
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            update public.episodes
            set audio_r2_key = %s,
                script_json = jsonb_set(script_json, '{cues}', %s::jsonb)
            where id = %s
            """,
            (audio_key, cues_json, episode_id),
        )


# ── Gen-1：HEAD 確認 ─────────────────────────────────────────────


async def _handle_gen1(row: EpisodeRow) -> Literal["skip", "missing"]:
    """HEAD 確認 audio_r2_key 指向的整集 mp3 還在 R2。"""
    assert row.audio_r2_key is not None  # classify_episode 保證
    try:
        exists = await asyncio.to_thread(r2.object_exists, row.audio_r2_key)
    except StorageError as exc:
        logger.warning("[backfill] slug=%s HEAD 檢查失敗，當作找不到：%s", row.slug, exc)
        return "missing"
    return "skip" if exists else "missing"


# ── Gen-2：重新合成 ───────────────────────────────────────────────


async def _download_segments(keys: Sequence[str], workdir: Path) -> list[Path]:
    paths: list[Path] = []
    for idx, key in enumerate(keys):
        data = await asyncio.to_thread(r2.get_object, key)
        path = workdir / f"seg_{idx:03d}.mp3"
        path.write_bytes(data)
        paths.append(path)
    return paths


async def _process_gen2(row: EpisodeRow, workdir: Path) -> PlanResult:
    """下載 segments → SynthSegment → plan_layout/concat_episode/build_timeline
    → 與舊 cues 合併。跟 render_episode（engine/media/__init__.py）走同一支
    audio.py 佈局邏輯，唯一差別是音檔來源是「重下載舊 segment」而非「重新 TTS」。
    """
    script_lines = _script_lines(row.script_json)
    old_cues = _old_cues(row.script_json)
    keys = row.audio_r2_keys
    if not (len(keys) == len(script_lines) == len(old_cues)):
        raise ValueError(
            f"segment 數（{len(keys)}）/ script 行數（{len(script_lines)}）/ "
            f"cues 行數（{len(old_cues)}）不一致"
        )

    seg_paths = await _download_segments(keys, workdir)

    segs = [
        SynthSegment(
            index=idx,
            speaker=str(line.get("speaker", "")),
            text=str(line.get("text", "")),
            zh=str(line.get("zh", "")),
            audio_path=path,
            duration=_probe_duration(path),
            pause_before=bool(line.get("pause_before", False)),
        )
        for idx, (path, line) in enumerate(zip(seg_paths, script_lines, strict=True))
    ]

    settings = get_settings()
    rate, channels = probe_stream_info(segs[0].audio_path)
    short_gap_path = workdir / "_silence_short.mp3"
    short_gap = synthesize_silence(
        short_gap_path, seconds=settings.pause_sec, rate=rate, channels=channels
    )
    if settings.long_pause_sec == settings.pause_sec:
        long_gap_path, long_gap = short_gap_path, short_gap
    else:
        long_gap_path = workdir / "_silence_long.mp3"
        long_gap = synthesize_silence(
            long_gap_path, seconds=settings.long_pause_sec, rate=rate, channels=channels
        )

    layout = plan_layout(segs, short_gap=short_gap, long_gap=long_gap)

    mp3_path = workdir / "episode.mp3"
    probed_duration = concat_episode(
        segs,
        layout,
        mp3_path,
        workdir,
        short_gap_path=short_gap_path,
        long_gap_path=long_gap_path,
    )
    diff_from_layout = abs(probed_duration - layout[-1].end)
    if diff_from_layout > 0.05:
        raise TTSError(
            f"整集 concat 後物理時長（{probed_duration:.3f}s）與佈局預測"
            f"（{layout[-1].end:.3f}s）差超過容許誤差 0.05s"
        )

    new_cues = build_timeline(segs, layout)
    merged_cues = _merge_cues(new_cues, old_cues)

    old_last_end = float(old_cues[-1].get("end", 0.0)) if old_cues else 0.0
    return PlanResult(
        slug=row.slug,
        segment_count=len(segs),
        new_duration=probed_duration,
        old_last_end=old_last_end,
        diff=probed_duration - old_last_end,
        mp3_path=mp3_path,
        cues=merged_cues,
    )


async def _apply_gen2(row: EpisodeRow, plan: PlanResult) -> bool:
    """上傳整集 mp3 → 成功後 UPDATE。回傳 DB UPDATE 是否成功。

    R2 上傳失敗（StorageError/OSError）不在這裡攔——直接往上炸給呼叫端記錄
    成「處理失敗」；DB UPDATE 失敗攔在這裡記成「已上傳但未落庫」，不是同一
    種失敗（R2 已經是新內容，冪等重跑會再試一次 UPDATE，不需要整集重算）。
    """
    key = f"episodes/{row.id}/episode.mp3"
    data = plan.mp3_path.read_bytes()
    await asyncio.to_thread(r2.put_object, key, data, "audio/mpeg")
    try:
        await _update_episode(row.id, key, plan.cues)
    except psycopg.Error as exc:
        logger.error(
            "[backfill] slug=%s R2 上傳成功（key=%s）但 DB UPDATE 失敗，"
            "重跑會自動補：%s",
            row.slug,
            key,
            exc,
        )
        return False
    return True


# ── 驅動 ────────────────────────────────────────────────────────


async def _run(limit: int, slug: str | None, apply: bool) -> Stats:
    rows = await _fetch_episodes(limit, slug)
    stats = Stats()

    for row in rows:
        if classify_episode(row.audio_r2_key) == "gen1":
            outcome = await _handle_gen1(row)
            if outcome == "skip":
                stats.gen1_skip += 1
                logger.info("[backfill] slug=%s Gen-1 整集檔已存在，skip", row.slug)
            else:
                stats.gen1_missing.append(row.slug)
                logger.warning(
                    "[backfill] slug=%s audio_r2_key=%s 指向的物件不存在，需人工判斷",
                    row.slug,
                    row.audio_r2_key,
                )
            continue

        with tempfile.TemporaryDirectory(prefix=f"dc_backfill_full_{row.slug}_") as td:
            workdir = Path(td)
            try:
                plan = await _process_gen2(row, workdir)
            except (TTSError, StorageError, OSError, KeyError, ValueError) as exc:
                logger.error("[backfill] slug=%s Gen-2 處理失敗：%s", row.slug, exc)
                stats.errors.append((row.slug, str(exc)))
                continue

            stats.gen2_planned += 1
            logger.info(
                "[backfill] slug=%s Gen-2 segments=%d 新時長=%.3fs "
                "舊 cues[-1].end=%.3fs diff=%+.3fs",
                plan.slug,
                plan.segment_count,
                plan.new_duration,
                plan.old_last_end,
                plan.diff,
            )

            if not apply:
                continue

            try:
                db_ok = await _apply_gen2(row, plan)
            except (StorageError, OSError) as exc:
                logger.error("[backfill] slug=%s R2 上傳失敗：%s", row.slug, exc)
                stats.errors.append((row.slug, str(exc)))
                continue

            if db_ok:
                stats.gen2_applied += 1
            else:
                stats.gen2_db_failed.append(row.slug)

    return stats


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    p.add_argument("--limit", type=int, default=50, help="本批處理集數上限（預設 50，--slug 時忽略）")
    p.add_argument("--slug", type=str, default=None, help="指定單集 slug（忽略 --limit）")
    p.add_argument("--apply", action="store_true", help="實際上傳 R2 並寫 DB；預設只 dry-run")
    a = p.parse_args()

    async def _main() -> Stats:
        # pool 的背景 worker task 綁在建立當下的 event loop；open/use/close 全部
        # 包在同一個 coroutine、同一次 asyncio.run() 內才安全（同 backfill_segments.py）。
        try:
            return await _run(a.limit, a.slug, a.apply)
        finally:
            await close_pool()

    stats = asyncio.run(_main())

    mode = "APPLY" if a.apply else "DRY-RUN"
    print(f"\n=== {mode} 完成 ===")
    print(f"Gen-1 skip（整集檔已存在）        : {stats.gen1_skip}")
    print(f"Gen-1 missing（需人工判斷）        : {len(stats.gen1_missing)}")
    for s in stats.gen1_missing:
        print(f"  - {s}")
    if a.apply:
        print(f"Gen-2 已上傳並落庫                : {stats.gen2_applied}")
        print(f"Gen-2 已上傳但 DB UPDATE 失敗      : {len(stats.gen2_db_failed)}")
        for s in stats.gen2_db_failed:
            print(f"  - {s}")
    else:
        print(f"Gen-2 待處理（計畫已驗證，未寫入）  : {stats.gen2_planned}")
    print(f"錯誤                              : {len(stats.errors)}")
    for s, msg in stats.errors:
        print(f"  - {s}: {msg}")


if __name__ == "__main__":
    _amain()
