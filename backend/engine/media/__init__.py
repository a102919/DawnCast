"""engine.media：把 ScriptJSON 渲染成逐行 mp3 + 字幕成品。

對外公開高階入口 render_episode：TTS → timeline → 字幕字串，
全部產到傳入的 workdir，不寫死 output/、不上傳 R2（上傳是 upload_artifacts_node 的事）。
不再燒字幕 mp4 — 前端吃 Cue list 自行做同步高亮；也不再 ffmpeg concat 整集
mp3 — 每行一個 mp3 給前端 Web Audio API 串接播，字幕與音檔數學上對齊。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.config import get_settings
from shared.models import Cue, ScriptJSON

from .subtitles import build_timeline, cues_to_json, write_srt, write_vtt
from .tts import SynthSegment, synth_script
from .workdir import make_job_workdir

__all__ = [
    "Cue",
    "EpisodeArtifacts",
    "SegmentArtifact",
    "SynthSegment",
    "build_timeline",
    "cues_to_json",
    "make_job_workdir",
    "render_episode",
    "synth_script",
    "write_srt",
    "write_vtt",
]


@dataclass(frozen=True)
class SegmentArtifact:
    """單行 TTS 產物：音檔路徑 + 真實時長。"""

    index: int
    audio_path: Path
    duration: float


@dataclass(frozen=True)
class EpisodeArtifacts:
    """一集渲染完成的成品：逐行 segments、字幕字串與時間軸 cues。

    不再含整集 mp3（拿掉 mp3_path 欄位；新方案下「整集 mp3」概念消失——
    音檔由多個 per-line segment 串接而成，沒有獨立的整集檔）。
    """

    segments: list[SegmentArtifact]
    srt: str
    vtt: str
    cues: list[Cue]


async def render_episode(
    script: ScriptJSON, workdir: Path, *, cefr: str = "B1"
) -> EpisodeArtifacts:
    """把腳本渲染成逐行 mp3 + 字幕字串 + cues 時間軸，全部產到 workdir。

    cefr 決定 TTS 語速（A2 慢速輸入，見 tts.CEFR_SPEED / _CEFR_RATE_EDGE）。

    不再 ffmpeg concat 整集 mp3：每行獨立 mp3 在 synth_script 內已寫到
    workdir/line_NNN_speaker.mp3，這裡直接把 SynthSegment 轉成 SegmentArtifact。
    """
    settings = get_settings()
    workdir.mkdir(parents=True, exist_ok=True)

    segs = await synth_script(script, workdir, cefr=cefr)

    cues = build_timeline(
        segs,
        settings.pause_sec,
        long_pause_sec=settings.long_pause_sec,
    )
    srt = write_srt(cues)
    vtt = write_vtt(cues)

    segments = [
        SegmentArtifact(index=i, audio_path=seg.audio_path, duration=seg.duration)
        for i, seg in enumerate(segs)
    ]

    return EpisodeArtifacts(
        segments=segments,
        srt=srt,
        vtt=vtt,
        cues=cues,
    )
