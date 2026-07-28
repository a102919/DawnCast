"""音訊工具：concat_segments（保留 export 但不主流程呼叫）、cut_segment（backfill 用）。

concat_segments 留著是給舊測試 / mock 路徑當 escape hatch 仍可產整集 mp3；
新流程（render_episode）不再呼叫，由前端 Web Audio API 自行串接逐行 mp3。

cut_segment 是 backfill script 的工具：從既有整集 mp3 用 ffmpeg -c copy 切出
指定時間區段，frame-boundary 對齊，誤差 ≤ 1 個 mp3 frame（~26ms @44.1kHz）。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from shared.errors import TTSError

from .tts import SynthSegment, _probe_duration


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
) -> float:
    """把 segs 的音檔依序串接成 out_mp3，行與行之間插入靜音。

    回傳最終 mp3 的物理時長（秒，ffprobe format=duration）；caller 拿去當
    build_timeline 的 target_duration 對齊字幕時間軸（見模組 docstring）。

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

    # 6) 量測最終 mp3 物理時長回傳（libmp3lame 重編碼會加 frame alignment
    #    overhead，每段約 +50ms 累積，前端 audio.currentTime 對齊到 mp3 frame，
    #    cues 必須以這個時長為 ground truth 等比縮放）
    return _probe_duration(out_mp3)


def cut_segment(in_mp3: Path, start: float, end: float, out_mp3: Path) -> None:
    """從整集 mp3 用 ffmpeg -c copy 切出 [start, end] 區段寫到 out_mp3。

    給 backfill script 從既有整集 mp3 切段用。-c copy 不重編碼，
    frame-boundary 對齊，誤差 ≤ 1 個 mp3 frame（~26ms @44.1kHz）。
    比 ffmpeg -c:a libmp3lame 重編碼快 10x 且無 frame alignment 飄移。

    start/end 單位：秒；end-start > 0；in_mp3 必須存在。
    """
    if not in_mp3.exists():
        raise TTSError(f"cut_segment：輸入檔不存在 {in_mp3}")
    if end <= start:
        raise TTSError(f"cut_segment：end={end} <= start={start}")
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-i",
            str(in_mp3),
            "-t",
            str(end - start),
            "-c",
            "copy",
            str(out_mp3),
        ],
        what=f"切段 {start:.2f}-{end:.2f} → {out_mp3.name}",
    )
