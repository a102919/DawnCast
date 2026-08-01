"""音訊工具：plan_layout + concat_episode，整集 mp3 佈局與串接的單一事實來源。

concat 用 frame-level stream copy（`-f concat -c copy`），不重編碼：每行 mp3
已是 tts._trim_silence 產出的 128k CBR libmp3lame。最終檔時長＝Σ(每個輸入
檔案的 frame count 對應時長)，是 frame count 的函數，drift=0 是數學保證，
不需要事後拿物理時長反向 scale（對照 subtitles.build_timeline 的說明）。

兩個實測踩到的坑，處理方式都在這個模組集中解掉：

1. **LAME/Xing gapless tag 讓 ffprobe 說謊**：libmp3lame 預設會在檔案開頭寫一個
   Xing/LAME info tag，記錄 encoder 前後各自要跳過幾個 priming/padding 樣本，
   讓「獨立播放這個檔案」的 decoder 能算出跟原始輸入一致的「聽感時長」——
   ffprobe `format=duration` 讀的正是這個 tag，不是實際 frame 物理時長。
   concat demuxer `-c copy` 串接多個獨立編碼的 mp3 時，只有序列第一個檔案的
   tag 會被尊重，其餘檔案的 priming/padding 樣本會被當成真實可聽內容播出。
   若每個檔案的 duration 仍用「聽感時長」記帳，物理時長會逐檔累積偏移（實測：
   不關掉這個 tag，每個非首檔的物理貢獻比 format=duration 回報值多出 ~50ms，
   一集 40 行下來可以飄移到秒級）。修法：encode 時一律加 `-write_xing 0`
   （這裡的 `_synthesize_silence` 與 `tts._trim_silence` 都有），讓
   format=duration 變成純 frame 物理時長，才能真的 Σ 起來對齊。

2. **anullsrc `-t` 的 frame 量化不是冪等的**：同一個名目秒數重複餵給
   `-t`，量出來的實際時長會逐次多算一個 frame（實測：0.15 量出 0.216，
   把 0.216 再餵回去量出 0.264，不是穩定值）。所以「量測用的靜音檔」跟
   「真正併進整集的靜音檔」必須是同一個實體檔案，不能量完就丟、串接時
   拿量測值重新產生——這也是 concat_episode 要求呼叫端把 plan_layout
   用過的 short_gap_path / long_gap_path 原檔傳進來，而不是自己依 layout
   反推靜音時長重新產生的原因。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from shared.errors import TTSError

from .tts import SynthSegment, _probe_duration

GapKind = Literal["short", "long", "none"]


@dataclass(frozen=True)
class LayoutEntry:
    """一行在整集 mp3 內的精確位置。plan_layout 的輸出，是 concat_episode 與
    subtitles.build_timeline 共用的唯一事實來源——cursor 累積邏輯只在
    plan_layout 這一份，其餘兩處只讀不算。
    """

    index: int
    start: float
    end: float
    gap_after: GapKind


def _run(cmd: list[str], *, what: str) -> None:
    """跑 ffmpeg / ffprobe，失敗時包成 TTSError（不洩漏完整 stderr 給上層）。"""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise TTSError(f"音訊處理失敗：{what}") from exc


def probe_stream_info(path: Path) -> tuple[int, int]:
    """ffprobe 量測音檔的 (sample_rate, channels)。concat 前用來 assert 全體一致——

    -c copy 混取樣率不會報錯，只會產生播放異常（變速/雜音）的壞檔，必須在
    Python 層先擋。用 config 名目值不可信：MiniMax 出 32kHz、edge-tts 出 24kHz。
    """
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        rate_str, channels_str = probe.stdout.strip().split(",")
        return int(rate_str), int(channels_str)
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise TTSError(f"ffprobe 量測音訊參數失敗：{path.name}") from exc


def synthesize_silence(out_path: Path, *, seconds: float, rate: int, channels: int) -> float:
    """產生一段名目 seconds 秒的靜音 mp3 到 out_path，回傳 ffprobe 量測到的實際時長。

    回傳值才是 plan_layout 該吃的 gap 參數；out_path 這個實體檔案要原封不動傳給
    concat_episode 的 short_gap_path / long_gap_path 重用（見模組 docstring：
    frame 量化不是冪等的，量測用檔跟串接用檔必須是同一份，不能拿量測值重新產生）。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    channel_layout = "stereo" if channels == 2 else "mono"
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={rate}:cl={channel_layout}",
            "-t",
            str(seconds),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            "-write_xing",
            "0",
            str(out_path),
        ],
        what=f"產生 {seconds}s 靜音",
    )
    return _probe_duration(out_path)


def plan_layout(
    segs: Sequence[SynthSegment], *, short_gap: float, long_gap: float
) -> list[LayoutEntry]:
    """算每行在整集 mp3 內的精確 start/end；cursor 累積邏輯只存在這一份。

    short_gap / long_gap 必須是 synthesize_silence 的量測回傳值，不是名目值
    （否則 concat_episode 併進去的靜音實際時長會跟這裡預測的時間戳飄移）。

    gap_after 依下一行 pause_before 決定：True → long，否則 short；末行固定
    none（最後一行後不留靜音，cues[-1].end 直接等於整集檔物理時長）。
    """
    if not segs:
        raise TTSError("plan_layout：沒有任何 segment")
    layout: list[LayoutEntry] = []
    cursor = 0.0
    last = len(segs) - 1
    for idx, seg in enumerate(segs):
        start = round(cursor, 3)
        end = round(start + seg.duration, 3)
        if idx == last:
            gap_after: GapKind = "none"
            gap = 0.0
        elif segs[idx + 1].pause_before:
            gap_after = "long"
            gap = long_gap
        else:
            gap_after = "short"
            gap = short_gap
        layout.append(LayoutEntry(index=idx, start=start, end=end, gap_after=gap_after))
        cursor = end + gap
    return layout


def concat_episode(
    segs: Sequence[SynthSegment],
    layout: Sequence[LayoutEntry],
    out_mp3: Path,
    workdir: Path,
    *,
    short_gap_path: Path | None = None,
    long_gap_path: Path | None = None,
) -> float:
    """依 layout 把 segs 的音檔 frame-level 串接成 out_mp3（`-c copy`，不重編碼）。

    short_gap_path / long_gap_path：plan_layout 呼叫前 synthesize_silence 產出的
    「同一份」靜音檔（見模組 docstring：不能靠 layout 的 start/end 差值反推時長、
    現場重新產生，frame 量化不是冪等的）。layout 裡用到哪種 gap_after 就必須
    傳對應的路徑，缺了會 TTSError。

    concat 前 assert 全部 segment、以及實際用到的靜音檔，都與第一行 segment
    同 sample_rate/channels。

    回傳 ffprobe(out_mp3) 的實際物理時長；caller 要 assert 這個值與
    layout[-1].end 的差在容許誤差內（|probe - layout[-1].end| <= 0.05）。
    """
    if not segs:
        raise TTSError("concat_episode：沒有任何 segment 可串接")
    if len(segs) != len(layout):
        raise TTSError("concat_episode：segs 與 layout 長度不一致")

    rate, channels = probe_stream_info(segs[0].audio_path)
    for seg in segs[1:]:
        seg_rate, seg_channels = probe_stream_info(seg.audio_path)
        if (seg_rate, seg_channels) != (rate, channels):
            raise TTSError(
                f"第 {seg.index} 行取樣率/聲道（{seg_rate}Hz/{seg_channels}ch）與"
                f"第 0 行（{rate}Hz/{channels}ch）不一致，-c copy 混率會產生壞檔"
            )

    gap_paths: dict[GapKind, Path | None] = {
        "short": short_gap_path,
        "long": long_gap_path,
        "none": None,
    }
    for kind, path in (("short", short_gap_path), ("long", long_gap_path)):
        if path is None:
            continue
        gap_rate, gap_channels = probe_stream_info(path)
        if (gap_rate, gap_channels) != (rate, channels):
            raise TTSError(
                f"{kind} 靜音檔取樣率/聲道（{gap_rate}Hz/{gap_channels}ch）與"
                f"segment（{rate}Hz/{channels}ch）不一致，-c copy 混率會產生壞檔"
            )

    workdir.mkdir(parents=True, exist_ok=True)
    list_lines: list[str] = []
    for seg, entry in zip(segs, layout, strict=True):
        list_lines.append(f"file '{seg.audio_path.resolve()}'")
        if entry.gap_after == "none":
            continue
        gap_path = gap_paths[entry.gap_after]
        if gap_path is None:
            raise TTSError(f"concat_episode：layout 需要 {entry.gap_after} 靜音檔但沒有提供")
        list_lines.append(f"file '{gap_path.resolve()}'")

    list_file = workdir / "_concat_list.txt"
    list_file.write_text("\n".join(list_lines) + "\n", encoding="utf-8")

    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(out_mp3),
        ],
        what="整集 concat",
    )

    list_file.unlink(missing_ok=True)

    return _probe_duration(out_mp3)
