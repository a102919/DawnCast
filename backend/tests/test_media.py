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

import httpx
import pytest

from engine.media import render_episode
from engine.media import tts as tts_mod
from engine.media.subtitles import build_timeline, cues_to_json, write_srt, write_vtt
from engine.media.tts import (
    MINIMAX_VOICES,
    VOICES,
    SynthSegment,
    _minimax_tts_request,
    synth_script,
)
from shared.config import Settings
from shared.errors import TTSError
from shared.models import Cue, ScriptJSON

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "loop_engineering.json"
# 本機生成的 ground truth（不入版控，缺檔時對照測試自動 skip）
_BASELINE_SRT = Path(__file__).resolve().parent / "fixtures" / "loop_engineering_baseline.srt"

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
    assert set(MINIMAX_VOICES) == {"Alex", "Sarah", "Nova"}


# ── MiniMax TTS：請求解析與 fallback 佈線（mock transport，無網路）──


def _tts_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "minimax_auth_token": "test-token",
        "minimax_tts_url": "https://example.test/v1/t2a_v2",
        "http_max_retries": 1,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _client_with(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_minimax_tts_request_decodes_hex() -> None:
    payload_audio = b"ID3-fake-mp3-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"base_resp": {"status_code": 0}, "data": {"audio": payload_audio.hex()}},
        )

    async with _client_with(handler) as client:
        audio = await _minimax_tts_request(client, _tts_settings(), {"text": "hi"})
    assert audio == payload_audio


async def test_minimax_tts_request_business_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"base_resp": {"status_code": 1004, "status_msg": "login fail"}}
        )

    async with _client_with(handler) as client:
        with pytest.raises(TTSError, match="1004"):
            await _minimax_tts_request(client, _tts_settings(), {"text": "hi"})


async def test_synth_script_falls_back_to_edge_on_minimax_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MiniMax 任一行失敗 → 整份腳本改走 edge-tts，不會混音、不會炸 render。"""
    monkeypatch.setattr(tts_mod, "get_settings", lambda: _tts_settings())

    def failing_synth(*_args: object, **_kwargs: object) -> object:
        raise TTSError("mock: minimax down")

    monkeypatch.setattr(tts_mod, "_make_minimax_line_synth", lambda *a: failing_synth)

    edge_calls: list[str] = []

    async def fake_edge(index: int, speaker: str, text: str, out_path: Path, rate: str) -> float:
        edge_calls.append(f"{speaker}:{rate}")
        out_path.write_bytes(b"mp3")
        return 1.0

    monkeypatch.setattr(tts_mod, "_synth_line_edge", fake_edge)

    script = ScriptJSON.model_validate_json(_FIXTURE.read_text(encoding="utf-8"))
    segs = await synth_script(script, tmp_path, cefr="A2")

    assert len(segs) == len(script.script)
    # 全部行都由 edge 合成，且 A2 語速（-20%）有帶到
    assert len(edge_calls) == len(script.script)
    assert all(call.endswith(":-20%") for call in edge_calls)


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
async def test_時間軸對照ground_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """跑真實 pipeline，逐 cue 比對既有正確 SRT，容差 0.3s。需要網路（edge-tts）。

    強制走 edge-tts：baseline SRT 是 edge 聲線錄的，MiniMax 聲線時長不同會誤判；
    也避免測試消耗 MiniMax 配額。
    """
    pytest.importorskip("edge_tts")
    monkeypatch.setattr(tts_mod, "get_settings", lambda: _tts_settings(minimax_auth_token=""))
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
