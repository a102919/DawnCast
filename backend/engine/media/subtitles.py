"""字幕：從 SynthSegment 算時間軸，產 SRT / VTT / JSON 字串。

時間軸在記憶體一次算完（cursor += dur + pause），下游全吃 Cue list，
不再 glob mp3 後整批 ffprobe。前端依 Cue list 自行做同步高亮，不再生 mp4。
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
    target_duration: float | None = None,
) -> list[Cue]:
    """依序為每段指派 start/end 時間戳（cursor += dur + pause），回傳 Cue list。

    long_pause_sec 必須跟 audio.concat_segments 傳的值一致，否則字幕時間軸
    會跟實際串接出的音檔脫鉤（兩邊各自算，但用同一套規則：下一段標
    pause_before=True 時，這一段之後的停頓拉長）。

    target_duration 若提供且與 cues[-1].end 偏差 ≥ 0.1%，整段 cues 等比例縮放
    讓尾端對齊該值（mp3 物理時長，ffprobe format=duration）。原因：concat_segments
    把每段 mp3 → WAV → libmp3lame 重編碼會引入 LAME frame alignment overhead，
    最終 mp3 物理時長跟 sum(seg.duration + pauses) 累積差幾十 ms/段，前端
    audio.currentTime 對齊到 mp3 frame，cues 不縮放就會飄出音檔。差 < 0.1% 直接
    跳過避免浮點誤差。target_duration=None 向後相容舊行為。
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
    return _align_to_duration(cues, target_duration)


def _align_to_duration(cues: list[Cue], target_duration: float | None) -> list[Cue]:
    """若 target_duration 提供，把整段 cues 等比例縮放讓 cues[-1].end 對齊。

    差 < 0.1% 不動（量測浮點誤差會觸發無意義的 scale）；Cue 為 Pydantic model，
    用 model_copy(update=...) 走不可變更新（守 coding rules 禁直接 mutation）。
    """
    if target_duration is None or not cues:
        return cues
    current_end = cues[-1].end
    if current_end <= 0:
        return cues
    factor = target_duration / current_end
    if abs(factor - 1.0) < 0.001:
        return cues
    return [
        cue.model_copy(
            update={
                "start": round(cue.start * factor, 3),
                "end": round(cue.end * factor, 3),
            }
        )
        for cue in cues
    ]


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
