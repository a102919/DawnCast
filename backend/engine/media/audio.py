"""音訊串接：把逐行 mp3 串成單一 episode.mp3，行間插靜音。

搬 POC concat 邏輯：過 wav 統一取樣率（mp3 concat demuxer 對不同來源不可靠）、
行間插 pause_sec 靜音、用絕對路徑寫 concat list → libmp3lame 128k。
臨時 wav 寫在傳入的 workdir，結束清掉。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from shared.errors import TTSError

from .tts import SynthSegment


def _run(cmd: list[str], *, what: str) -> None:
    """跑 ffmpeg / ffprobe，失敗時包成 TTSError（不洩漏完整 stderr 給上層）。"""
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise TTSError(f"音訊處理失敗：{what}") from exc


def concat_segments(
    segs: Sequence[SynthSegment],
    out_mp3: Path,
    *,
    pause_sec: float,
    sample_rate: int,
    long_pause_sec: float | None = None,
) -> None:
    """把 segs 的音檔依序串接成 out_mp3，行與行之間插入靜音。

    long_pause_sec 給 chapter/話題轉換邊界用（下一行 pause_before=True 時，
    這一行「之後」的停頓拉長）；缺省時退化成現有均一 pause_sec 行為。
    """
    if not segs:
        raise TTSError("concat_segments：沒有任何 segment 可串接")
    long_pause = pause_sec if long_pause_sec is None else long_pause_sec

    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    wav_dir = out_mp3.parent / "_wav"
    wav_dir.mkdir(exist_ok=True)

    # 1) 產生短停頓靜音 wav；長停頓與短停頓不同時才多產一份
    silence_short = wav_dir / "silence_short.wav"
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={sample_rate}:cl=mono",
            "-t",
            str(pause_sec),
            str(silence_short),
        ],
        what="產生短靜音",
    )
    if long_pause != pause_sec:
        silence_long = wav_dir / "silence_long.wav"
        _run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={sample_rate}:cl=mono",
                "-t",
                str(long_pause),
                str(silence_long),
            ],
            what="產生長靜音",
        )
    else:
        silence_long = silence_short

    # 2) 每行 mp3 → 統一取樣率的 mono wav
    wavs: list[Path] = []
    for seg in segs:
        wav = wav_dir / f"line_{seg.index:03d}.wav"
        _run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(seg.audio_path),
                "-ar",
                str(sample_rate),
                "-ac",
                "1",
                str(wav),
            ],
            what=f"轉檔第 {seg.index} 行",
        )
        wavs.append(wav)

    # 3) 串接清單：line1, silence, line2, silence, ...（下一行標 chapter 邊界時用長靜音）
    list_file = wav_dir / "concat.txt"
    lines = []
    for idx, w in enumerate(wavs):
        lines.append(f"file '{w.resolve()}'")
        nxt = segs[idx + 1] if idx + 1 < len(segs) else None
        silence = silence_long if (nxt is not None and nxt.pause_before) else silence_short
        lines.append(f"file '{silence.resolve()}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 4) concat → 編碼成最終 mp3
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
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(out_mp3),
        ],
        what="串接編碼 mp3",
    )

    # 5) 清掉臨時 wav
    for w in wavs:
        w.unlink(missing_ok=True)
    silence_short.unlink(missing_ok=True)
    if silence_long != silence_short:
        silence_long.unlink(missing_ok=True)
    list_file.unlink(missing_ok=True)
    wav_dir.rmdir()
