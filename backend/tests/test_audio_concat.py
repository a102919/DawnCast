"""engine.media.audio 測試：真 ffmpeg 合成極短 fixture，驗證 plan_layout + concat_episode。

跳過條件：本機沒有 ffmpeg / ffprobe（CI/開發機都有，不預期常態跳過）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from engine.media.audio import concat_episode, plan_layout, probe_stream_info, synthesize_silence
from engine.media.tts import SynthSegment, _probe_duration
from shared.errors import TTSError

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="缺少 ffmpeg / ffprobe",
)


def _make_tone(path: Path, *, seconds: float, rate: int = 24000, channels: int = 1) -> None:
    """用 lavfi 產一段正弦波 mp3，當極短 segment fixture（不需要真 TTS）。

    -write_xing 0 對齊真實 tts._trim_silence 的輸出（見該函式與 audio._synthesize_silence
    的說明）：不寫 LAME gapless tag，讓 ffprobe format=duration 回報 frame 物理時長，
    fixture 才會如實反映 concat_episode 在生產環境會遇到的檔案特性。
    """
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
            "-ac",
            str(channels),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-write_xing",
            "0",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _seg(index: int, path: Path, *, pause_before: bool = False) -> SynthSegment:
    return SynthSegment(
        index=index,
        speaker="Alex",
        text=f"line {index}",
        zh=f"第{index}行",
        audio_path=path,
        duration=_probe_duration(path),
        pause_before=pause_before,
    )


@pytest.fixture
def three_segs(tmp_path: Path) -> list[SynthSegment]:
    """3 行極短 fixture：第 2 行標 pause_before=True，驗 gap 分派。"""
    durations = [0.4, 0.6, 0.3]
    paths = [tmp_path / f"line_{i:03d}.mp3" for i in range(3)]
    for p, seconds in zip(paths, durations, strict=True):
        _make_tone(p, seconds=seconds)
    return [
        _seg(0, paths[0]),
        _seg(1, paths[1], pause_before=True),
        _seg(2, paths[2]),
    ]


def test_plan_layout_gap分派與累積(three_segs: list[SynthSegment]) -> None:
    layout = plan_layout(three_segs, short_gap=0.1, long_gap=0.2)

    # seg[1].pause_before=True → seg[0] 之後用 long；seg[2] 之前(seg[1] 之後)用 short；末行 none。
    assert [e.gap_after for e in layout] == ["long", "short", "none"]

    d = [s.duration for s in three_segs]
    assert layout[0].start == 0.0
    assert layout[0].end == round(d[0], 3)
    assert layout[1].start == round(d[0] + 0.2, 3)
    assert layout[1].end == round(layout[1].start + d[1], 3)
    assert layout[2].start == round(layout[1].end + 0.1, 3)
    assert layout[2].end == round(layout[2].start + d[2], 3)


def test_plan_layout_空segments_raise() -> None:
    with pytest.raises(TTSError):
        plan_layout([], short_gap=0.1, long_gap=0.2)


def _make_gaps(
    three_segs: list[SynthSegment], tmp_path: Path, *, short_sec: float, long_sec: float
) -> tuple[float, float, Path, Path]:
    """產生 short/long 靜音檔，回傳 (short_gap, long_gap, short_path, long_path)。

    量測用檔跟串接用檔必須是同一份實體檔案（見 audio.py 模組 docstring：
    anullsrc -t 的 frame 量化不是冪等的），這裡的路徑要原封傳給 concat_episode。
    """
    rate, channels = probe_stream_info(three_segs[0].audio_path)
    short_path = tmp_path / "_silence_short.mp3"
    long_path = tmp_path / "_silence_long.mp3"
    short_gap = synthesize_silence(short_path, seconds=short_sec, rate=rate, channels=channels)
    long_gap = synthesize_silence(long_path, seconds=long_sec, rate=rate, channels=channels)
    return short_gap, long_gap, short_path, long_path


def test_concat_episode_物理時長對齊layout預測(
    three_segs: list[SynthSegment], tmp_path: Path
) -> None:
    """concat 用真實量測 gap（非名目值）算佈局，串接後物理時長要對齊 layout[-1].end。"""
    short_gap, long_gap, short_path, long_path = _make_gaps(
        three_segs, tmp_path, short_sec=0.15, long_sec=0.35
    )

    layout = plan_layout(three_segs, short_gap=short_gap, long_gap=long_gap)
    out_mp3 = tmp_path / "episode.mp3"
    probed = concat_episode(
        three_segs, layout, out_mp3, tmp_path, short_gap_path=short_path, long_gap_path=long_path
    )

    assert out_mp3.exists() and out_mp3.stat().st_size > 0
    assert abs(probed - layout[-1].end) <= 0.05


def test_concat_episode_異取樣率raise_TTSError(tmp_path: Path) -> None:
    """-c copy 混取樣率不會報錯只會產壞檔，concat 前必須主動擋。"""
    p0 = tmp_path / "a.mp3"
    p1 = tmp_path / "b.mp3"
    _make_tone(p0, seconds=0.3, rate=24000)
    _make_tone(p1, seconds=0.3, rate=32000)
    bad_segs = [_seg(0, p0), _seg(1, p1)]
    layout = plan_layout(bad_segs, short_gap=0.1, long_gap=0.1)

    with pytest.raises(TTSError):
        concat_episode(bad_segs, layout, tmp_path / "out.mp3", tmp_path)


def test_concat_episode_異聲道raise_TTSError(tmp_path: Path) -> None:
    p0 = tmp_path / "a.mp3"
    p1 = tmp_path / "b.mp3"
    _make_tone(p0, seconds=0.3, rate=24000, channels=1)
    _make_tone(p1, seconds=0.3, rate=24000, channels=2)
    bad_segs = [_seg(0, p0), _seg(1, p1)]
    layout = plan_layout(bad_segs, short_gap=0.1, long_gap=0.1)

    with pytest.raises(TTSError):
        concat_episode(bad_segs, layout, tmp_path / "out.mp3", tmp_path)


def test_concat_episode_segs與layout長度不一致raise(
    three_segs: list[SynthSegment], tmp_path: Path
) -> None:
    layout = plan_layout(three_segs, short_gap=0.1, long_gap=0.2)
    with pytest.raises(TTSError):
        concat_episode(three_segs, layout[:-1], tmp_path / "out.mp3", tmp_path)


def test_concat_episode_最後一行不留靜音(three_segs: list[SynthSegment], tmp_path: Path) -> None:
    """末行 gap_after=none：cues[-1].end 直接等於整集檔物理時長，不多留尾靜音。"""
    short_gap, long_gap, short_path, long_path = _make_gaps(
        three_segs, tmp_path, short_sec=0.1, long_sec=0.2
    )
    layout = plan_layout(three_segs, short_gap=short_gap, long_gap=long_gap)
    assert layout[-1].gap_after == "none"
    out_mp3 = tmp_path / "episode.mp3"
    probed = concat_episode(
        three_segs, layout, out_mp3, tmp_path, short_gap_path=short_path, long_gap_path=long_path
    )
    assert abs(probed - layout[-1].end) <= 0.05


def test_concat_episode_缺靜音檔raise(three_segs: list[SynthSegment], tmp_path: Path) -> None:
    """layout 需要 short/long 靜音但呼叫端沒給對應路徑：TTSError，不能悄悄跳過該段靜音。"""
    layout = plan_layout(three_segs, short_gap=0.1, long_gap=0.2)
    with pytest.raises(TTSError):
        concat_episode(three_segs, layout, tmp_path / "out.mp3", tmp_path)
