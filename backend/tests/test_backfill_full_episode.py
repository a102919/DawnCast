"""scripts.backfill_full_episode 測試：分流邏輯 + cues 合併 + 批次驅動。

全部 mock R2 / DB，不打真網路、不跑真 ffmpeg（_process_gen2 本身在驅動測試裡
被 monkeypatch 掉，真正的 ffmpeg pipeline 邏輯由 test_audio_concat.py 覆蓋）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest

from scripts import backfill_full_episode as bf
from shared.errors import StorageError, TTSError
from shared.models import Cue, WordOffset

# ── 分流邏輯 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("audio_r2_key", "expected"),
    [
        ("episodes/abc/episode.mp3", "gen1"),  # Gen-1：整集檔本來就在
        (None, "gen2"),  # 從沒寫過整集 key
        ("", "gen2"),  # 空字串
        ("episodes/abc/segments/000.mp3", "gen2"),  # 污染值：舊版誤寫 audio_keys[0]
    ],
)
def test_classify_episode(audio_r2_key: str | None, expected: str) -> None:
    assert bf.classify_episode(audio_r2_key) == expected


# ── cues 合併 ──────────────────────────────────────────────────


def _new_cue(index: int, start: float, end: float) -> Cue:
    return Cue(index=index, speaker="Alex", text="NEW TEXT", zh="新文字", start=start, end=end)


def test_merge_cues_uses_new_timing_and_old_text() -> None:
    new_cues = [_new_cue(1, 0.0, 1.0), _new_cue(2, 1.3, 2.0)]
    old_cues = [
        {
            "index": 1,
            "speaker": "Alex",
            "text": "old line one",
            "zh": "舊句一",
            "start": 0.0,
            "end": 1.2,
            "words": [{"word": "old", "start": 0.0, "end": 0.3}],
        },
        {
            "index": 2,
            "speaker": "Sarah",
            "text": "old line two",
            "zh": "舊句二",
            "start": 1.5,
            "end": 2.3,
        },
    ]

    merged = bf._merge_cues(new_cues, old_cues)

    assert [c.start for c in merged] == [0.0, 1.3]
    assert [c.end for c in merged] == [1.0, 2.0]
    assert merged[0].text == "old line one"
    assert merged[0].zh == "舊句一"
    assert merged[0].speaker == "Alex"
    assert merged[0].words == [WordOffset(word="old", start=0.0, end=0.3)]
    assert merged[1].speaker == "Sarah"
    assert merged[1].words is None


def test_merge_cues_length_mismatch_raises() -> None:
    new_cues = [_new_cue(1, 0.0, 1.0)]
    old_cues = [
        {"index": 1, "speaker": "Alex", "text": "a", "zh": "甲", "start": 0.0, "end": 1.0},
        {"index": 2, "speaker": "Sarah", "text": "b", "zh": "乙", "start": 1.2, "end": 2.0},
    ]
    with pytest.raises(ValueError, match="行數不一致"):
        bf._merge_cues(new_cues, old_cues)


def test_script_lines_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        bf._script_lines({})


def test_old_cues_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        bf._old_cues({"script": []})


# ── 批次驅動（_run）：monkeypatch IO 邊界 ─────────────────────────


def _row(
    *,
    slug: str = "ep-1",
    audio_r2_key: str | None,
    audio_r2_keys: list[str] | None = None,
) -> bf.EpisodeRow:
    return bf.EpisodeRow(
        id="00000000-0000-0000-0000-000000000001",
        slug=slug,
        audio_r2_key=audio_r2_key,
        audio_r2_keys=audio_r2_keys or [],
        script_json={},
    )


async def test_run_gen1_skip_when_object_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row(audio_r2_key="episodes/1/episode.mp3")

    async def fake_fetch(limit: int, slug: str | None) -> list[bf.EpisodeRow]:
        return [row]

    monkeypatch.setattr(bf, "_fetch_episodes", fake_fetch)
    monkeypatch.setattr(bf.r2, "object_exists", lambda key: True)

    stats = await bf._run(limit=10, slug=None, apply=False)

    assert stats.gen1_skip == 1
    assert stats.gen1_missing == []


async def test_run_gen1_missing_when_object_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row(audio_r2_key="episodes/1/episode.mp3")

    async def fake_fetch(limit: int, slug: str | None) -> list[bf.EpisodeRow]:
        return [row]

    monkeypatch.setattr(bf, "_fetch_episodes", fake_fetch)
    monkeypatch.setattr(bf.r2, "object_exists", lambda key: False)

    stats = await bf._run(limit=10, slug=None, apply=False)

    assert stats.gen1_skip == 0
    assert stats.gen1_missing == ["ep-1"]


def _fake_plan(slug: str) -> bf.PlanResult:
    return bf.PlanResult(
        slug=slug,
        segment_count=2,
        new_duration=3.0,
        old_last_end=3.05,
        diff=-0.05,
        mp3_path=Path("/tmp/does-not-matter/episode.mp3"),
        cues=[Cue(index=1, speaker="Alex", text="a", zh="甲", start=0.0, end=3.0)],
    )


async def test_run_gen2_dry_run_skips_upload_and_db(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row(audio_r2_key=None, audio_r2_keys=["episodes/1/segments/000.mp3"])
    calls: dict[str, Any] = {"put_object": 0, "update": 0}

    async def fake_fetch(limit: int, slug: str | None) -> list[bf.EpisodeRow]:
        return [row]

    async def fake_process(row: bf.EpisodeRow, workdir: Path) -> bf.PlanResult:
        return _fake_plan(row.slug)

    def fake_put_object(key: str, data: bytes, content_type: str) -> None:
        calls["put_object"] += 1

    async def fake_update(episode_id: str, audio_key: str, cues: list[Cue]) -> None:
        calls["update"] += 1

    monkeypatch.setattr(bf, "_fetch_episodes", fake_fetch)
    monkeypatch.setattr(bf, "_process_gen2", fake_process)
    monkeypatch.setattr(bf.r2, "put_object", fake_put_object)
    monkeypatch.setattr(bf, "_update_episode", fake_update)

    stats = await bf._run(limit=10, slug=None, apply=False)

    assert stats.gen2_planned == 1
    assert stats.gen2_applied == 0
    assert calls == {"put_object": 0, "update": 0}


async def test_run_gen2_apply_uploads_then_updates_db(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _row(audio_r2_key=None, audio_r2_keys=["episodes/1/segments/000.mp3"])
    calls: dict[str, Any] = {"put_object": [], "update": []}

    async def fake_fetch(limit: int, slug: str | None) -> list[bf.EpisodeRow]:
        return [row]

    async def fake_process(row: bf.EpisodeRow, workdir: Path) -> bf.PlanResult:
        return _fake_plan(row.slug)

    def fake_put_object(key: str, data: bytes, content_type: str) -> None:
        calls["put_object"].append(key)

    async def fake_update(episode_id: str, audio_key: str, cues: list[Cue]) -> None:
        calls["update"].append((episode_id, audio_key))

    monkeypatch.setattr(bf, "_fetch_episodes", fake_fetch)
    monkeypatch.setattr(bf, "_process_gen2", fake_process)
    monkeypatch.setattr(bf.r2, "put_object", fake_put_object)
    monkeypatch.setattr(bf, "_update_episode", fake_update)
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: b"fake-mp3-bytes"
    )  # PlanResult.mp3_path 不是真檔案

    stats = await bf._run(limit=10, slug=None, apply=True)

    assert stats.gen2_applied == 1
    assert stats.gen2_db_failed == []
    assert calls["put_object"] == [f"episodes/{row.id}/episode.mp3"]
    assert calls["update"] == [(row.id, f"episodes/{row.id}/episode.mp3")]


async def test_run_gen2_apply_db_failure_recorded_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(audio_r2_key=None, audio_r2_keys=["episodes/1/segments/000.mp3"])

    async def fake_fetch(limit: int, slug: str | None) -> list[bf.EpisodeRow]:
        return [row]

    async def fake_process(row: bf.EpisodeRow, workdir: Path) -> bf.PlanResult:
        return _fake_plan(row.slug)

    def fake_put_object(key: str, data: bytes, content_type: str) -> None:
        return None

    async def failing_update(episode_id: str, audio_key: str, cues: list[Cue]) -> None:
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(bf, "_fetch_episodes", fake_fetch)
    monkeypatch.setattr(bf, "_process_gen2", fake_process)
    monkeypatch.setattr(bf.r2, "put_object", fake_put_object)
    monkeypatch.setattr(bf, "_update_episode", failing_update)
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"fake-mp3-bytes")

    stats = await bf._run(limit=10, slug=None, apply=True)

    # R2 已經上傳成功（fake_put_object 沒炸），只有 DB 失敗——不算進 errors，
    # 算進 gen2_db_failed（冪等重跑會自動補，不需要人工介入 R2 那半）。
    assert stats.gen2_applied == 0
    assert stats.gen2_db_failed == ["ep-1"]
    assert stats.errors == []


async def test_run_gen2_process_failure_recorded_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(audio_r2_key=None, audio_r2_keys=["episodes/1/segments/000.mp3"])

    async def fake_fetch(limit: int, slug: str | None) -> list[bf.EpisodeRow]:
        return [row]

    async def failing_process(row: bf.EpisodeRow, workdir: Path) -> bf.PlanResult:
        raise TTSError("整集 concat 後物理時長與佈局預測差超過容許誤差")

    monkeypatch.setattr(bf, "_fetch_episodes", fake_fetch)
    monkeypatch.setattr(bf, "_process_gen2", failing_process)

    stats = await bf._run(limit=10, slug=None, apply=True)

    assert stats.gen2_planned == 0
    assert len(stats.errors) == 1
    assert stats.errors[0][0] == "ep-1"


async def test_run_gen2_upload_failure_recorded_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(audio_r2_key=None, audio_r2_keys=["episodes/1/segments/000.mp3"])

    async def fake_fetch(limit: int, slug: str | None) -> list[bf.EpisodeRow]:
        return [row]

    async def fake_process(row: bf.EpisodeRow, workdir: Path) -> bf.PlanResult:
        return _fake_plan(row.slug)

    def failing_put_object(key: str, data: bytes, content_type: str) -> None:
        raise StorageError("物件上傳失敗")

    monkeypatch.setattr(bf, "_fetch_episodes", fake_fetch)
    monkeypatch.setattr(bf, "_process_gen2", fake_process)
    monkeypatch.setattr(bf.r2, "put_object", failing_put_object)
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"fake-mp3-bytes")

    stats = await bf._run(limit=10, slug=None, apply=True)

    assert stats.gen2_applied == 0
    assert stats.gen2_db_failed == []
    assert len(stats.errors) == 1
    assert stats.errors[0][0] == "ep-1"
