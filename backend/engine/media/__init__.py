"""engine.media：把 ScriptJSON 渲染成 mp3 + 字幕成品。

對外公開高階入口 render_episode：TTS → concat → timeline → 字幕字串，
全部產到傳入的 workdir，不寫死 output/、不上傳 R2（上傳是 upload_artifacts_node 的事）。
不再燒字幕 mp4 — 前端吃 Cue list 自行做同步高亮。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shared.config import get_settings
from shared.models import Cue, ScriptJSON

from .audio import concat_segments
from .subtitles import build_timeline, cues_to_json, write_srt, write_vtt
from .tts import SynthSegment, synth_script
from .workdir import make_job_workdir

__all__ = [
    "Cue",
    "EpisodeArtifacts",
    "SynthSegment",
    "build_timeline",
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
    """一集渲染完成的成品：音檔、字幕字串與時間軸 cues。"""

    mp3_path: Path
    srt: str
    vtt: str
    cues: list[Cue]


async def render_episode(
    script: ScriptJSON, workdir: Path, *, cefr: str = "B1"
) -> EpisodeArtifacts:
    """把腳本渲染成 mp3 + 字幕字串 + cues 時間軸，全部產到 workdir。

    cefr 決定 TTS 語速（A2 慢速輸入，見 tts.CEFR_SPEED / _CEFR_RATE_EDGE）。
    """
    settings = get_settings()
    workdir.mkdir(parents=True, exist_ok=True)

    segs = await synth_script(script, workdir, cefr=cefr)

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

    return EpisodeArtifacts(
        mp3_path=mp3_path,
        srt=srt,
        vtt=vtt,
        cues=cues,
    )
