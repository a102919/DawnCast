"""字幕：從 SynthSegment 算時間軸，產 SRT / VTT / JSON 字串。

時間軸在記憶體一次算完（cursor += dur + pause），下游全吃 Cue list，
不再 glob mp3 後整批 ffprobe。前端依 Cue list 自行做同步高亮，不再生 mp4。

不再做 _align_to_duration 等比縮放：新方案下音檔由前端 Web Audio API
逐行串接播，每行 mp3 是它自己的真實時長，cue 時間軸即 ground truth，
不需要再用最終 mp3 物理時長反向校正。
"""

from __future__ import annotations

from collections.abc import Sequence

from shared.models import Cue

from .tts import SynthSegment


def build_timeline(
    segs: Sequence[SynthSegment],
    pause_sec: float,
    *,
    long_pause_sec: float | None = None,
) -> list[Cue]:
    """依序為每段指派 start/end 時間戳（cursor += dur + pause），回傳 Cue list。

    long_pause_sec 必須跟音檔邊界插的靜音長度一致（目前 concat_segments
    已拿掉，但 pause_before 邊界的長靜音仍由前端在 source.onended 後 setTimeout
    模擬），否則字幕時間軸會跟前端實際播放脫鉤。
    """
    long_pause = pause_sec if long_pause_sec is None else long_pause_sec
    cues: list[Cue] = []
    cursor = 0.0
    for idx, seg in enumerate(segs):
        start = cursor
        end = start + seg.duration
        cues.append(
            Cue(
                index=idx + 1,
                speaker=seg.speaker,
                text=seg.text,
                zh=seg.zh,
                start=round(start, 3),
                end=round(end, 3),
            )
        )
        nxt = segs[idx + 1] if idx + 1 < len(segs) else None
        pause = long_pause if (nxt is not None and nxt.pause_before) else pause_sec
        cursor = end + pause
    return cues


def _fmt_ts(seconds: float) -> str:
    """SRT 時戳 HH:MM:SS,mmm（逗號）。"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds - int(seconds)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_ts(seconds: float) -> str:
    """WebVTT 時戳：把逗號換成點。"""
    return _fmt_ts(seconds).replace(",", ".")


def write_srt(cues: Sequence[Cue]) -> str:
    """產 SRT 字串（EN 上、ZH 下，逗號時戳）。"""
    chunks = []
    for cue in cues:
        chunks.append(
            f"{cue.index}\n{_fmt_ts(cue.start)} --> {_fmt_ts(cue.end)}\n{cue.text}\n{cue.zh}\n"
        )
    return "\n".join(chunks)


def write_vtt(cues: Sequence[Cue]) -> str:
    """產 WebVTT 字串（EN 上、ZH 下，點號時戳）。"""
    chunks = ["WEBVTT", ""]
    for cue in cues:
        chunks.extend(
            [
                f"{cue.index}",
                f"{_fmt_vtt_ts(cue.start)} --> {_fmt_vtt_ts(cue.end)}",
                cue.text,
                cue.zh,
                "",
            ]
        )
    return "\n".join(chunks)


def cues_to_json(cues: Sequence[Cue]) -> list[dict[str, object]]:
    """Cue list → camelCase dict list（前端播放頁直接吃）。"""
    # ponytail: 砍掉 burn_video 之後 mp4 不再生，前端只吃 Cue list 自己 render；
    # raw srt/vtt 字串留著備用
    return [cue.model_dump(by_alias=True) for cue in cues]
