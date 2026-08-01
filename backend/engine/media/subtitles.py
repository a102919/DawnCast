"""字幕：從 SynthSegment + LayoutEntry 算時間軸，產 SRT / VTT / JSON 字串。

cue.start/end 直接抄 audio.LayoutEntry（cursor 累積邏輯只在 plan_layout 算一次），
下游全吃 Cue list，不再 glob mp3 後整批 ffprobe。前端依 Cue list 自行做同步高亮，
不再生 mp4。

不再做 _align_to_duration 等比縮放：整集 mp3 由 concat_episode frame-level
stream copy 產生，drift=0 是數學保證（見 audio.py 模組 docstring），不需要
再用最終 mp3 物理時長反向校正。
"""

from __future__ import annotations

from collections.abc import Sequence

from shared.models import Cue, WordOffset

from .audio import LayoutEntry
from .tts import SynthSegment


def build_timeline(segs: Sequence[SynthSegment], layout: Sequence[LayoutEntry]) -> list[Cue]:
    """依 layout（plan_layout 的輸出）為每段填 start/end 時間戳，回傳 Cue list。

    word_offsets：從 SynthSegment.word_offsets 帶進 Cue.words（詞級字幕；練習模式
    word click 用）。edge-tts fallback 給空 list（不存）。
    """
    cues: list[Cue] = []
    for seg, entry in zip(segs, layout, strict=True):
        # 轉成 API 端的 WordOffset（共用 dataclass 是 tts.WordOffset，但 Cue 期待
        # shared.models.api.WordOffset；兩個欄位相同但型別系統視為不同）。
        words: list[WordOffset] | None = (
            [WordOffset(word=w.word, start=w.start_sec, end=w.end_sec) for w in seg.word_offsets]
            if seg.word_offsets
            else None
        )
        cues.append(
            Cue(
                index=entry.index + 1,
                speaker=seg.speaker,
                text=seg.text,
                zh=seg.zh,
                start=entry.start,
                end=entry.end,
                words=words,
            )
        )
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
    """Cue list → camelCase dict list（前端播放頁直接吃）。

    words=None 的 cue 不帶 words 欄位（向後相容舊 client；前端 words 缺欄位
    走 cue-level click fallback）。
    """
    # ponytail: 砍掉 burn_video 之後 mp4 不再生，前端只吃 Cue list 自己 render；
    # raw srt/vtt 字串留著備用
    out: list[dict[str, object]] = []
    for cue in cues:
        d = cue.model_dump(by_alias=True)
        if d.get("words") is None:
            d.pop("words", None)
        out.append(d)
    return out
