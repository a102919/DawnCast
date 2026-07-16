"""engine.media：把 ScriptJSON 渲染成可播放的媒體成品。

對外公開高階入口 render_episode：TTS → concat → timeline → 字幕 → 燒影片，
全部產到傳入的 workdir，不寫死 output/、不上傳 R2（上傳是 Phase 3 的事）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.config import get_settings
from shared.models import Cue, ScriptJSON

from .audio import concat_segments
from .subtitles import build_timeline, burn_video, cues_to_json, write_srt, write_vtt
from .tts import SynthSegment, synth_script
from .workdir import make_job_workdir

__all__ = [
    "Cue",
    "EpisodeArtifacts",
    "SynthSegment",
    "build_timeline",
    "burn_video",
    "concat_segments",
    "cues_to_json",
    "make_job_workdir",
    "render_episode",
    "synth_script",
    "write_srt",
    "write_vtt",
]


@dataclass(frozen=True)
class EpisodeArtifacts:
    """一集渲染完成的成品：音檔、影片、字幕字串與時間軸 cues。"""

    mp3_path: Path
    mp4_path: Path
    srt: str
    vtt: str
    cues: list[Cue]


async def render_episode(script: ScriptJSON, workdir: Path) -> EpisodeArtifacts:
    """把腳本完整渲染成媒體成品，全部產到 workdir。"""
    settings = get_settings()
    workdir.mkdir(parents=True, exist_ok=True)

    segs = await synth_script(script, workdir)

    mp3_path = workdir / "episode.mp3"
    concat_segments(
        segs,
        mp3_path,
        pause_sec=settings.pause_sec,
        sample_rate=settings.sample_rate,
        long_pause_sec=settings.long_pause_sec,
    )

    cues = build_timeline(segs, settings.pause_sec, long_pause_sec=settings.long_pause_sec)
    srt = write_srt(cues)
    vtt = write_vtt(cues)

    mp4_path = workdir / "episode.mp4"
    burn_video(cues, mp3_path, mp4_path, workdir=workdir)

    return EpisodeArtifacts(
        mp3_path=mp3_path,
        mp4_path=mp4_path,
        srt=srt,
        vtt=vtt,
        cues=cues,
    )
