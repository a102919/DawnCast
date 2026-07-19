"""media 模組測試。

兩類：
1. 純邏輯（無網路、無 ffmpeg）：build_timeline / write_srt / write_vtt / cues_to_json。
2. 端對端時間軸對照：跑真實 TTS + concat + 字幕，逐 cue 比對既有 ground truth SRT，
   容差 0.3s（WordBoundary 與 ffprobe 微差可接受）。網路 / ffmpeg 不在時自動 skip。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from engine.media import render_episode
from engine.media.subtitles import build_timeline, cues_to_json, write_srt, write_vtt
from engine.media.tts import VOICES, SynthSegment
from shared.models import Cue, ScriptJSON

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "scripts" / "loop_engineering.json"
_BASELINE_SRT = _ROOT / "output" / "loop_engineering" / "subtitles.srt"

# 容差：每個 cue 的 start/end 與 ground truth 差異超過此值才算問題。
_TOLERANCE = 0.3


# ── 純邏輯測試 ────────────────────────────────────────────────


def _seg(
    index: int, speaker: str, text: str, zh: str, dur: float, *, pause_before: bool = False
) -> SynthSegment:
    return SynthSegment(
        index=index,
        speaker=speaker,
        text=text,
        zh=zh,
        audio_path=Path(f"/tmp/_fake_{index}.mp3"),
        duration=dur,
        pause_before=pause_before,
    )


def test_build_timeline_累積游標含停頓() -> None:
    segs = [
        _seg(0, "Alex", "hello", "哈囉", 2.0),
        _seg(1, "Sarah", "world", "世界", 3.0),
    ]
    cues = build_timeline(segs, pause_sec=0.3)
    assert [(c.start, c.end) for c in cues] == [(0.0, 2.0), (2.3, 5.3)]
    assert [c.index for c in cues] == [1, 2]
    assert [c.speaker for c in cues] == ["Alex", "Sarah"]


def test_build_timeline_chapter邊界用長停頓() -> None:
    """下一段標 pause_before=True → 前一段之後用 long_pause_sec，不是 pause_sec。"""
    segs = [
        _seg(0, "Nova", "chapter one", "第一章", 2.0),
        _seg(1, "Nova", "chapter two", "第二章", 3.0, pause_before=True),
    ]
    cues = build_timeline(segs, pause_sec=0.3, long_pause_sec=0.7)
    assert [(c.start, c.end) for c in cues] == [(0.0, 2.0), (2.7, 5.7)]


def test_write_srt_格式_en上zh下逗號時戳() -> None:
    cues = [Cue(index=1, speaker="Alex", text="Hi there", zh="嗨", start=0.0, end=7.464)]
    srt = write_srt(cues)
    assert srt == "1\n00:00:00,000 --> 00:00:07,464\nHi there\n嗨\n"


def test_write_vtt_標頭與點號時戳() -> None:
    cues = [Cue(index=1, speaker="Alex", text="Hi", zh="嗨", start=1.5, end=2.25)]
    vtt = write_vtt(cues)
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:01.500 --> 00:00:02.250" in vtt


def test_cues_to_json_camelCase() -> None:
    cues = [Cue(index=1, speaker="Alex", text="Hi", zh="嗨", start=0.0, end=1.0)]
    data = cues_to_json(cues)
    assert data == [
        {"index": 1, "speaker": "Alex", "text": "Hi", "zh": "嗨", "start": 0.0, "end": 1.0}
    ]


def test_voices_兩位主持人與單人口白都有對應() -> None:
    assert set(VOICES) == {"Alex", "Sarah", "Nova"}


# ── 端對端時間軸對照 ──────────────────────────────────────────


def _parse_srt_cues(text: str) -> list[tuple[float, float]]:
    """從 SRT 字串抽出每個 cue 的 (start, end) 秒數。"""

    def to_seconds(ts: str) -> float:
        hh, mm, rest = ts.split(":")
        ss, ms = rest.split(",")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000

    cues: list[tuple[float, float]] = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        m = re.match(r"(\d+:\d+:\d+,\d+) --> (\d+:\d+:\d+,\d+)", lines[1])
        if m:
            cues.append((to_seconds(m.group(1)), to_seconds(m.group(2))))
    return cues


def _tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.mark.skipif(not _BASELINE_SRT.exists(), reason="缺少 ground truth SRT")
@pytest.mark.skipif(not _tools_available(), reason="缺少 ffmpeg / ffprobe")
async def test_時間軸對照ground_truth(tmp_path: Path) -> None:
    """跑真實 pipeline，逐 cue 比對既有正確 SRT，容差 0.3s。需要網路（edge-tts）。"""
    pytest.importorskip("edge_tts")
    script = ScriptJSON.model_validate_json(_FIXTURE.read_text(encoding="utf-8"))

    try:
        artifacts = await render_episode(script, tmp_path)
    except Exception as exc:  # 多半是 edge-tts 連不上網
        pytest.skip(f"端對端渲染失敗（可能無網路）：{exc}")

    base = _parse_srt_cues(_BASELINE_SRT.read_text(encoding="utf-8"))
    new = _parse_srt_cues(artifacts.srt)

    assert len(new) == len(base) == len(script.script)

    for i, ((bs, be), (ns, ne)) in enumerate(zip(base, new, strict=True), 1):
        assert abs(bs - ns) <= _TOLERANCE, f"cue {i} start 差 {abs(bs - ns):.3f}s"
        assert abs(be - ne) <= _TOLERANCE, f"cue {i} end 差 {abs(be - ne):.3f}s"

    assert artifacts.mp3_path.exists() and artifacts.mp3_path.stat().st_size > 0
