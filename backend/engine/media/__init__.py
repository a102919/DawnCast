"""engine.media：把 ScriptJSON 渲染成整集 mp3 + 逐行 mp3 + 字幕成品。

對外公開高階入口 render_episode：TTS → 靜音量測 → 佈局 → concat → timeline
→ 字幕字串，全部產到傳入的 workdir，不寫死 output/、不上傳 R2（上傳是
upload_artifacts_node 的事）。不再燒字幕 mp4 — 前端吃 Cue list 自行做同步
高亮。整集 mp3 由 concat_episode frame-level stream copy 產生（不重編碼，
drift=0），逐行 mp3 仍保留（雙寫過渡期，見 EpisodeArtifacts docstring）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from shared.config import get_settings
from shared.errors import TTSError
from shared.models import Cue, ScriptJSON

from .audio import concat_episode, plan_layout, probe_stream_info, synthesize_silence
from .subtitles import build_timeline, cues_to_json, write_srt, write_vtt
from .tts import SynthSegment, WordOffset, synth_script
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
    """單行 TTS 產物：音檔路徑 + 真實時長 + 詞級字幕（練習模式 word click 用）。

    word_offsets 從 SynthSegment 帶上來（MiniMax 路徑有、edge-tts fallback 空 list）。
    """

    index: int
    audio_path: Path
    duration: float
    word_offsets: list[WordOffset] = field(default_factory=list)


@dataclass(frozen=True)
class EpisodeArtifacts:
    """一集渲染完成的成品：整集 mp3、逐行 segments、字幕字串與時間軸 cues。

    segments 欄位保留（雙寫過渡期：upload_artifacts_node 兩者都上傳，前端
    尚未切到單一整集 mp3 播放），Phase 4 前端切完再停產。
    """

    segments: list[SegmentArtifact]
    srt: str
    vtt: str
    cues: list[Cue]
    mp3_path: Path
    tts_provider: str = ""
    tts_characters: int = 0


async def render_episode(
    script: ScriptJSON, workdir: Path, *, cefr: str = "B1"
) -> EpisodeArtifacts:
    """把腳本渲染成整集 mp3 + 逐行 mp3 + 字幕字串 + cues 時間軸，全部產到 workdir。

    cefr 決定 TTS 語速（A2 慢速輸入，見 tts.CEFR_SPEED / _CEFR_RATE_EDGE）。

    流程：synth_script 逐行合成 → probe 第一行取樣率/聲道 → 產靜音量測實際
    gap → plan_layout 算佈局 → concat_episode frame-level 串成整集 mp3
    → build_timeline 依佈局填 cues。concat 後物理時長與 layout 預測差超過
    0.05s 視為佈局跟音檔脫鉤，直接 TTSError（不該發生，發生就是 bug）。
    """
    settings = get_settings()
    workdir.mkdir(parents=True, exist_ok=True)

    segs, tts_provider = await synth_script(script, workdir, cefr=cefr)
    tts_characters = sum(len(seg.text) for seg in segs)

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
    if abs(probed_duration - layout[-1].end) > 0.05:
        raise TTSError(
            f"整集 concat 後物理時長（{probed_duration:.3f}s）與佈局預測"
            f"（{layout[-1].end:.3f}s）差超過容許誤差"
        )

    cues = build_timeline(segs, layout)
    srt = write_srt(cues)
    vtt = write_vtt(cues)

    segments = [
        SegmentArtifact(
            index=i,
            audio_path=seg.audio_path,
            duration=seg.duration,
            word_offsets=list(seg.word_offsets),
        )
        for i, seg in enumerate(segs)
    ]

    return EpisodeArtifacts(
        segments=segments,
        srt=srt,
        vtt=vtt,
        cues=cues,
        mp3_path=mp3_path,
        tts_provider=tts_provider,
        tts_characters=tts_characters,
    )
