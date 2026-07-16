"""TTS：把 ScriptJSON 逐行用 edge-tts 合成，回傳帶真實時長的 segment list。

核心設計：TTS 一次回傳帶時長的結構，下游不再用檔名當索引、不再 glob 後整批 ffprobe。

時長一律以「該行音檔的真實時長」為準（對單一檔做一次 ffprobe，非整批 glob）。
原因：下游 concat 串接的是整個音檔；WordBoundary 只量到語音收尾，會比實際檔案短
約 0.05s/行，21 行累積成 >1s，導致字幕與實際播放音訊脫鉤。時間軸必須跟「會播出的
音訊」對齊，所以用檔案時長。WordBoundary 仍在 stream 過程取得，留作日後逐字高亮用，
但不拿來定時間軸。boundary 全缺席（純標點 / 數字行）時行為一致，無特殊情況。

注意（edge-tts 7.2.8 已驗證的限制）：
- 會 XML-escape 掉 SSML 標籤，無法靠 SSML 控制。
- 單次呼叫只收單一 voice，不能用一段 SSML 做雙人合成。
故只能逐行合成、逐行指定 voice。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts

from shared.errors import TTSError
from shared.models import ScriptJSON

# 主持人 → edge-tts voice。雙人對話靠逐行切 voice，不是 SSML。
# Nova：單人口白格式用（PRD 重新設計 §3），刻意選 MultilingualNeural 系列
# ——比 Guy/JennyNeural 更自然，且音色明顯跟雙主持有區隔，聽感就能分辨模式。
VOICES: dict[str, str] = {
    "Alex": "en-US-GuyNeural",
    "Sarah": "en-US-JennyNeural",
    "Nova": "en-US-EmmaMultilingualNeural",
}


@dataclass(frozen=True)
class SynthSegment:
    """單行合成結果：文字、音檔路徑與該行真實時長（秒）。

    pause_before：該行是否為 chapter/話題轉換邊界，串接時前面該行的停頓
    要拉長（見 audio.concat_segments 的 long_pause_sec）。
    """

    index: int
    speaker: str
    text: str
    zh: str
    audio_path: Path
    duration: float
    pause_before: bool = False


def _probe_duration(audio_path: Path) -> float:
    """對單一音檔做一次 ffprobe 取時長（boundary 缺席時的 fallback）。"""
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(probe.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as exc:
        raise TTSError(f"ffprobe 量測時長失敗：{audio_path.name}") from exc


# edge-tts 每行合成檔實測固定帶 ~0.2s 起音靜音 + ~0.85-0.9s 收尾靜音（與文字長度無關，
# RMS 量測確認是真靜音、非量測誤差）。這段靜音會被 _probe_duration 算進該行時長，
# 字幕 cue 因此比實際講話時間多掛一秒左右，逐行看不明顯，但整集下來字幕會嚴重跟音訊脫鉤。
# 用「轉正常方向切開頭」+「反轉音訊再切一次開頭（=切原始尾端）再轉回來」去頭尾靜音，
# 句中自然停頓不受影響（duration 门槛只吃頭尾，不吃中段）。
_SILENCE_THRESHOLD = "-50dB"
_LEAD_TRIGGER_SEC = 0.05
_LEAD_KEEP_SEC = 0.05
_TAIL_TRIGGER_SEC = 0.15
_TAIL_KEEP_SEC = 0.15


def _trim_silence(src: Path, dst: Path) -> None:
    """修剪 src 頭尾靜音寫到 dst；trim 後空檔（極端安靜行）就退回用原始音檔。"""
    filt = (
        f"silenceremove=start_periods=1:start_duration={_LEAD_TRIGGER_SEC}:"
        f"start_threshold={_SILENCE_THRESHOLD}:start_silence={_LEAD_KEEP_SEC},"
        "areverse,"
        f"silenceremove=start_periods=1:start_duration={_TAIL_TRIGGER_SEC}:"
        f"start_threshold={_SILENCE_THRESHOLD}:start_silence={_TAIL_KEEP_SEC},"
        "areverse"
    )
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-af",
                filt,
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(dst),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise TTSError(f"修剪靜音失敗：{src.name}") from exc

    if not dst.exists() or dst.stat().st_size == 0:
        # 整行都很安靜（近似耳語/單字）被門檻誤判成全靜音砍光，保交付優先於完美：直接用原始檔。
        dst.write_bytes(src.read_bytes())


async def _synth_line(
    index: int,
    speaker: str,
    text: str,
    out_path: Path,
) -> float:
    """合成單行：stream 寫檔、去頭尾靜音，回傳修剪後的真實時長（秒）。"""
    voice = VOICES.get(speaker)
    if voice is None:
        raise TTSError(f"未知主持人 {speaker!r}，無對應 voice")

    raw_path = out_path.with_name(f"{out_path.stem}_raw{out_path.suffix}")
    comm = edge_tts.Communicate(text, voice)
    got_audio = False

    try:
        with raw_path.open("wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                    got_audio = True
                # WordBoundary/SentenceBoundary 仍會送來（日後逐字高亮可用），
                # 但時間軸以檔案真實時長為準，這裡不需累積。
    except Exception as exc:  # edge_tts 連線 / 串流錯誤
        raise TTSError(f"edge-tts 合成第 {index} 行失敗：{speaker}") from exc

    if not got_audio or raw_path.stat().st_size == 0:
        raise TTSError(f"edge-tts 第 {index} 行未產生音訊：{speaker}")

    _trim_silence(raw_path, out_path)
    raw_path.unlink(missing_ok=True)

    # 用修剪後音檔的真實時長（與 concat 串接的音訊一致），單檔 ffprobe。
    return _probe_duration(out_path)


async def synth_script(script: ScriptJSON, workdir: Path) -> list[SynthSegment]:
    """逐行合成整份腳本，回傳帶真實時長的 SynthSegment list（順序即播放順序）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    segments: list[SynthSegment] = []
    for i, line in enumerate(script.script):
        out_path = workdir / f"line_{i:03d}_{line.speaker}.mp3"
        duration = await _synth_line(i, line.speaker, line.text, out_path)
        segments.append(
            SynthSegment(
                index=i,
                speaker=line.speaker,
                text=line.text,
                zh=line.zh,
                audio_path=out_path,
                duration=duration,
                pause_before=line.pause_before,
            )
        )
    return segments
