"""TTS：把 ScriptJSON 逐行合成，回傳帶真實時長的 segment list。

Provider 兩層（整份腳本為單位切換，不逐行混音——中途換聲線聽感無法接受）：
  1. MiniMax speech（t2a_v2，同一顆訂閱 token，已實測可用）：聲線自然、
     speed 參數直接支援 CEFR 分級語速。
  2. edge-tts fallback：token 未設或 MiniMax 呼叫失敗（限流/斷線）時整份退回，
     免費、無配額，品質次之但保交付。

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

import asyncio
import logging
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import edge_tts
import httpx

from shared.config import Settings, get_settings
from shared.errors import TTSError
from shared.models import ScriptJSON

logger = logging.getLogger(__name__)

# 主持人 → edge-tts voice（fallback 用）。雙人對話靠逐行切 voice，不是 SSML。
VOICES: dict[str, str] = {
    "Alex": "en-US-GuyNeural",
    "Sarah": "en-US-JennyNeural",
    "Nova": "en-US-EmmaMultilingualNeural",
}

# 主持人 → MiniMax speech 聲線（主路徑）。從 /v1/get_voice 的 332 個系統聲線挑的：
# Alex/Sarah 男女氣質分明，Nova 用敘事者聲線與雙主持做出聽感區隔。
MINIMAX_VOICES: dict[str, str] = {
    "Alex": "English_magnetic_voiced_man",
    "Sarah": "English_Upbeat_Woman",
    "Nova": "English_expressive_narrator",
}

# CEFR → 語速。A2 學習者需要慢速輸入（自然語速 ~170wpm 對 A2 是牆），
# B1/B2 維持原速（向下相容既有集聽感）。0.8 曾跟句中的刪節號停頓疊加變得過慢
# （實測 1.7 wps），跟 prompt 端「別用 ... 表示停頓」的修正一起，速度調鬆到 0.85。
# ponytail: 固定三檔，之後要 per-user 微調再開進 user_settings。
CEFR_SPEED: dict[str, float] = {"A2": 0.85, "B1": 1.0, "B2": 1.0}  # MiniMax speed 參數
_CEFR_RATE_EDGE: dict[str, str] = {"A2": "-15%", "B1": "+0%", "B2": "+0%"}  # edge-tts rate

# MiniMax voice_setting.emotion 合法值。ScriptLine.emotion 是寬鬆 str（LLM prompt-instructed
# JSON 難免拼錯/给無效值），不在這個集合裡就當沒標，退化成現況行為（不帶 emotion key）。
_MINIMAX_EMOTIONS = frozenset(
    {"happy", "sad", "angry", "fearful", "disgusted", "surprised", "neutral"}
)


@dataclass(frozen=True)
class WordOffset:
    """單字在 segment 音檔內的時間戳（秒，相對於 segment 自己的 0）。

    start / end 是 trim 後 mp3 內的位置。word 是該字原始 text。
    用於練習模式 word click：點字 → seek 到 segment.start + word.start。
    """

    word: str
    start_sec: float
    end_sec: float


@dataclass(frozen=True)
class SynthSegment:
    """單行合成結果：文字、音檔路徑、該行真實時長（秒）、詞級時間戳。

    pause_before：該行是否為 chapter/話題轉換邊界，時間軸計算時前面該行的停頓
    要拉長（見 subtitles.build_timeline 的 long_pause_sec）。
    word_offsets：MiniMax TTS 路徑會拿到（詞級字幕）；edge-tts fallback 為空 list
    （edge-tts 雖有 WordBoundary event 但字幕精度差，且對齊中文 word 切分不穩，
    不存。空 list 時前端走 cue-level click fallback）。
    """

    index: int
    speaker: str
    text: str
    zh: str
    audio_path: Path
    duration: float
    pause_before: bool = False
    word_offsets: list[WordOffset] = field(default_factory=list)


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
# _LEAD_KEEP_SEC 原為 0.05：實測（edge-tts 真實音檔 RMS envelope）發現觸發點落在字首輔音
# 已經起音之後（約 -50dB→-11dB 的爬升段中段），keep=0.05 砍在爬升段中途，trim 後開頭直接
# 是 -30dB 起跳、20ms 內衝到 -12dB 的硬切，聽感就是「這個字含糊/被咬掉開頭」。
# keep=0.10 才能把整段自然爬升的攻擊尾（-80dB → -12dB，約 80ms）留住，開頭變成平滑淡入。
_SILENCE_THRESHOLD = "-50dB"
_LEAD_TRIGGER_SEC = 0.05
_LEAD_KEEP_SEC = 0.10
_TAIL_TRIGGER_SEC = 0.15
_TAIL_KEEP_SEC = 0.15


def _trim_silence(src: Path, dst: Path) -> None:
    """修剪 src 頭尾靜音寫到 dst；trim 後空檔（極端安靜行）就退回用原始音檔。

    兩段處理：
    1. silenceremove 去頭尾靜音（areverse 雙向砍）+ libmp3lame 重編碼。砍頭時保留
       _LEAD_KEEP_SEC（80ms 攻擊段）避免吃字首輔音。
    2. 重編碼後 libmp3lame 會重新注入 576 samples (~13ms @ 24kHz) 的開頭 encoder delay，
       接段時這 13ms 會在 segment N+1 開頭聽成可聞的「吃字頭」。第二段再用 -c copy
       + -ss 0.013 把這層 padding 切掉（frame-boundary seek 殘留 0-26ms，靠 cue 對齊
       吸收）。
    """
    filt = (
        f"silenceremove=start_periods=1:start_duration={_LEAD_TRIGGER_SEC}:"
        f"start_threshold={_SILENCE_THRESHOLD}:start_silence={_LEAD_KEEP_SEC},"
        "areverse,"
        f"silenceremove=start_periods=1:start_duration={_TAIL_TRIGGER_SEC}:"
        f"start_threshold={_SILENCE_THRESHOLD}:start_silence={_TAIL_KEEP_SEC},"
        "areverse"
    )
    intermediate = dst.with_name(f"{dst.stem}_trimmed{dst.suffix}")
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
                # 不寫 LAME/Xing gapless tag：整集 concat（engine.media.audio）靠
                # ffprobe format=duration 對每個 segment 逐檔記帳算佈局，這個 tag
                # 會讓 format=duration 回報「跳過 encoder priming 樣本後的聽感時長」
                # 而非 frame 物理時長；segment 在 concat 時序上除了第一行都不是
                # 檔案序列的首檔，priming 樣本會被當真實內容播出，兩者不一致就是
                # 逐行累積的時間軸飄移根源。關掉這個 tag 才能讓 duration 各自可加總。
                "-write_xing",
                "0",
                str(intermediate),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        # 第二段：去 LAME encoder delay。-ss 後 -c copy 走 frame boundary，殘留 0-26ms。
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                "0.013",
                "-i",
                str(intermediate),
                "-c",
                "copy",
                str(dst),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise TTSError(f"修剪靜音失敗：{src.name}") from exc
    finally:
        intermediate.unlink(missing_ok=True)

    if not dst.exists() or dst.stat().st_size == 0:
        # 整行都很安靜（近似耳語/單字）被門檻誤判成全靜音砍光，保交付優先於完美：直接用原始檔。
        dst.write_bytes(src.read_bytes())


async def _synth_line_edge(
    index: int,
    speaker: str,
    text: str,
    emotion: str | None,
    out_path: Path,
    rate: str = "+0%",
) -> float:
    """edge-tts 合成單行：stream 寫檔、去頭尾靜音，回傳修剪後的真實時長（秒）。

    emotion 吃進來直接忽略——edge-tts 不支援情緒調整，且 SSML markup 會被跳脫，
    這裡只是讓呼叫端跟 MiniMax 路徑共用同一個 synth_line 簽章。
    """
    voice = VOICES.get(speaker)
    if voice is None:
        raise TTSError(f"未知主持人 {speaker!r}，無對應 voice")

    raw_path = out_path.with_name(f"{out_path.stem}_raw{out_path.suffix}")
    comm = edge_tts.Communicate(text, voice, rate=rate)
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

    # _trim_silence/_probe_duration 是同步 subprocess.run 呼叫 ffmpeg/ffprobe，
    # 用 to_thread 避免擋住 worker event loop（同 dict_audio.py 的處理方式）。
    await asyncio.to_thread(_trim_silence, raw_path, out_path)
    raw_path.unlink(missing_ok=True)

    # 用修剪後音檔的真實時長（與 concat 串接的音訊一致），單檔 ffprobe。
    return await asyncio.to_thread(_probe_duration, out_path)


async def _fetch_word_boundary(client: httpx.AsyncClient, subtitle_url: str) -> list[WordOffset]:
    """抓 MiniMax subtitle_file 的詞級字幕 JSON，轉 WordOffset list。

    回傳格式（從實測 response 推斷，文件未列 schema）：
    [{"text": "你好", "start_time": 0, "end_time": 320}, ...]，單位 ms。
    抓失敗回空 list——詞級字幕缺失不該擋整行合成，前端 fallback 到 cue-level click。
    """
    try:
        resp = await client.get(subtitle_url)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:  # 下載/解析失敗都不該炸掉整行合成
        logger.warning("抓 word boundary 字幕失敗：%s", exc)
        return []
    if not isinstance(raw, list):
        return []
    out: list[WordOffset] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        word = entry.get("text")
        start_ms = entry.get("start_time")
        end_ms = entry.get("end_time")
        if not isinstance(word, str) or not word:
            continue
        if not isinstance(start_ms, (int, float)) or not isinstance(end_ms, (int, float)):
            continue
        start_sec = float(start_ms) / 1000.0
        end_sec = float(end_ms) / 1000.0
        out.append(WordOffset(word=word, start_sec=start_sec, end_sec=end_sec))
    return out


async def _minimax_tts_request(
    client: httpx.AsyncClient, settings: Settings, payload: dict[str, object]
) -> tuple[bytes, str | None]:
    """打 t2a_v2 拿 hex 音訊 + subtitle URL（給詞級字幕用）。

    timeout/5xx 退避重試 http_max_retries 次；429 / 4xx / 業務錯誤碼不重試，
    直接 TTSError（讓上層整份 fallback edge-tts）。

    回 (audio_bytes, subtitle_url | None)：subtitle 缺失（payload 沒開 subtitle_enable
    或 response 沒回 subtitle_file）時 subtitle_url 是 None。
    """
    last_exc: Exception | None = None
    for attempt in range(settings.http_max_retries + 1):
        try:
            resp = await client.post(settings.minimax_tts_url, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < settings.http_max_retries:
                await asyncio.sleep(min(2**attempt, 8) * 0.5)
                continue
            raise TTSError(f"MiniMax TTS 連線失敗：{type(exc).__name__}") from exc

        if resp.status_code >= 500:
            last_exc = TTSError(f"MiniMax TTS 回應 {resp.status_code}")
            if attempt < settings.http_max_retries:
                await asyncio.sleep(min(2**attempt, 8) * 0.5)
                continue
            raise TTSError(f"MiniMax TTS 回應 {resp.status_code}")
        if resp.status_code >= 400:
            raise TTSError(f"MiniMax TTS 回應 {resp.status_code}")

        data = resp.json()
        base = data.get("base_resp") or {}
        if base.get("status_code") != 0:
            raise TTSError(
                f"MiniMax TTS 業務錯誤 {base.get('status_code')}：{base.get('status_msg')}"
            )
        payload_data = data.get("data") or {}
        audio_hex = payload_data.get("audio")
        if not audio_hex:
            raise TTSError("MiniMax TTS 回應缺 audio 欄位")
        subtitle_url = payload_data.get("subtitle_file")
        try:
            audio = bytes.fromhex(audio_hex)
        except ValueError as exc:
            raise TTSError("MiniMax TTS audio 欄位非合法 hex") from exc
        return audio, (subtitle_url if isinstance(subtitle_url, str) and subtitle_url else None)

    raise TTSError("MiniMax TTS 重試耗盡") from last_exc


def _make_minimax_line_synth(
    client: httpx.AsyncClient, settings: Settings, speed: float
) -> Callable[[int, str, str, str | None, Path], Awaitable[tuple[float, list[WordOffset]]]]:
    """綁定 client/speed 的 MiniMax 單行合成器（與 edge 路徑同簽名）。

    回 (duration, word_offsets)：word_offsets 給 SynthSegment.word_offsets 用。
    """

    async def synth_line(
        _index: int, speaker: str, text: str, emotion: str | None, out_path: Path
    ) -> tuple[float, list[WordOffset]]:
        # _index：跟 edge 路徑共用同一個 Callable 簽名（_run_lines 呼叫端統一傳位置參數），
        # 這裡沒用到——MiniMax 錯誤訊息不需要行號。
        voice = MINIMAX_VOICES.get(speaker)
        if voice is None:
            raise TTSError(f"未知主持人 {speaker!r}，無對應 MiniMax voice")
        voice_setting: dict[str, object] = {"voice_id": voice, "speed": speed}
        if emotion in _MINIMAX_EMOTIONS:
            voice_setting["emotion"] = emotion
        audio, subtitle_url = await _minimax_tts_request(
            client,
            settings,
            {
                "model": settings.minimax_tts_model,
                "text": text,
                "voice_setting": voice_setting,
                "audio_setting": {"format": "mp3", "sample_rate": 32000},
                # 開詞級字幕（練習模式 word click 用）。字幕單位 ms，存在
                # subtitle_file URL；缺這個參數就拿不到詞級 boundary。
                "subtitle_enable": True,
                "subtitle_type": "word",
                "stream": False,
            },
        )
        raw_path = out_path.with_name(f"{out_path.stem}_raw{out_path.suffix}")
        raw_path.write_bytes(audio)
        # 與 edge 路徑同樣去頭尾靜音——供應商無論誰，cue 時間軸都要貼著實際語音。
        # to_thread：避免同步 subprocess.run 擋住 worker event loop。
        await asyncio.to_thread(_trim_silence, raw_path, out_path)
        raw_path.unlink(missing_ok=True)
        duration = await asyncio.to_thread(_probe_duration, out_path)
        # word boundary：抓 subtitle_file 拿詞級字幕。失敗不擋合成（空 list fallback）。
        word_offsets = await _fetch_word_boundary(client, subtitle_url) if subtitle_url else []
        return duration, word_offsets

    return synth_line


async def _edge_line_with_word_offsets(
    index: int,
    speaker: str,
    text: str,
    emotion: str | None,
    out_path: Path,
    rate: str,
) -> tuple[float, list[WordOffset]]:
    """edge-tts fallback：拿 duration 但不存 word_offsets（精度差，不上傳）。"""
    duration = await _synth_line_edge(index, speaker, text, emotion, out_path, rate)
    return duration, []


async def _run_lines(
    script: ScriptJSON,
    workdir: Path,
    synth_line: Callable[
        [int, str, str, str | None, Path], Awaitable[tuple[float, list[WordOffset]]]
    ],
) -> list[SynthSegment]:
    """逐行跑指定合成器，組出 SynthSegment list（順序即播放順序）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    segments: list[SynthSegment] = []
    for i, line in enumerate(script.script):
        out_path = workdir / f"line_{i:03d}_{line.speaker}.mp3"
        emotion = getattr(line, "emotion", None)
        duration, word_offsets = await synth_line(i, line.speaker, line.text, emotion, out_path)
        segments.append(
            SynthSegment(
                index=i,
                speaker=line.speaker,
                text=line.text,
                zh=line.zh,
                audio_path=out_path,
                duration=duration,
                pause_before=line.pause_before,
                word_offsets=word_offsets,
            )
        )
    return segments


async def synth_script(
    script: ScriptJSON, workdir: Path, *, cefr: str = "B1"
) -> tuple[list[SynthSegment], str]:
    """合成整份腳本。MiniMax 優先（token 有設時），任一行失敗整份 fallback edge-tts。

    整份為單位切換供應商：中途換聲線的聽感不可接受，寧可重跑已合成的行。
    cefr 決定語速（MiniMax 用 speed 參數，edge-tts 用 rate 字串）。

    回傳 (segments, provider)；provider ∈ {"minimax", "edge"}——edge-tts 免費，
    呼叫端算 TTS 成本時要用這個欄位排除 fallback 集數，不能只看字數。
    """
    settings = get_settings()
    if settings.minimax_auth_token:
        speed = CEFR_SPEED.get(cefr, 1.0)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=settings.http_connect_timeout,
                read=settings.http_read_timeout,
                write=settings.http_read_timeout,
                pool=settings.http_connect_timeout,
            ),
            headers={"Authorization": f"Bearer {settings.minimax_auth_token}"},
        ) as client:
            try:
                segs = await _run_lines(
                    script, workdir, _make_minimax_line_synth(client, settings, speed)
                )
                return segs, "minimax"
            except TTSError as exc:
                logger.warning("MiniMax TTS 失敗，整份腳本 fallback 到 edge-tts：%s", exc)

    rate = _CEFR_RATE_EDGE.get(cefr, "+0%")

    async def edge_line(
        index: int, speaker: str, text: str, emotion: str | None, out_path: Path
    ) -> tuple[float, list[WordOffset]]:
        return await _edge_line_with_word_offsets(index, speaker, text, emotion, out_path, rate)

    return await _run_lines(script, workdir, edge_line), "edge"
