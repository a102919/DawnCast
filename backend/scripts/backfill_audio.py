"""為 dict_cache 補 audio_url（Layer 1，Piper TTS）。

流程：
  1. 撈 dict_cache where audio_url is null（--limit 控制批大小）
  2. 對每個 word 跑 Piper TTS subprocess（單字只合成 baseform）
  3. 上傳：
     - R2 有設 → key=audio/dict/{word}.mp3，audio_url=R2 簽章 URL
     - 否則 → 寫到 {LOCAL_MEDIA_DIR}/dict/{word}.mp3，audio_url={PUBLIC}/media/dict/{word}.mp3
  4. UPDATE dict_cache.audio_url；失敗保留 null，待下批補。

授權：Piper 為 MIT（espeak-ng 採 dynamic linking，不污染自有程式碼授權）。
自合 mp3 不繼承上游 share-alike，完整 DawnCast 所有權。

執行（後台跑）：
  uv run python -m scripts.backfill_audio --limit 500

Piper 安裝：
  pip install piper-tts
  python -m piper.download_voices en_US-amy-medium
  # 模型預設路徑 ~/.local/share/piper/{voice}.onnx + .onnx.json
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from shared.config import get_settings
from shared.db.pool import close_pool, connection
from shared.errors import StorageError

logger = logging.getLogger(__name__)

_PIPER_TIMEOUT_SEC = 15  # 單字合成 <2s，留緩衝


def _piper_path() -> str:
    """piper 執行檔路徑（PATH 內或顯式 PIPER_BIN）。"""
    import os

    return os.environ.get("PIPER_BIN") or shutil.which("piper") or ""


def _synthesize(word: str, model: str) -> bytes:
    """subprocess 跑 Piper，回傳 mp3 bytes。

    Piper 介面：piper --model <voice.onnx> --output_file <out.wav> < textfile_or_stdin
    為簡化，把 word 寫到 stdin（單行）。輸出 wav，post 處轉 mp3（若無 ffmpeg 仍可吃 wav）。
    """
    bin_path = _piper_path()
    if not bin_path:
        raise RuntimeError("找不到 piper 執行檔；請安裝 piper-tts 或設 PIPER_BIN")
    if not Path(model).exists():
        raise FileNotFoundError(f"Piper 模型不存在：{model}")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = Path(tmpdir) / "out.wav"
        proc = subprocess.run(
            [bin_path, "--model", model, "--output_file", str(out_path)],
            input=(word + "\n").encode("utf-8"),
            capture_output=True,
            timeout=_PIPER_TIMEOUT_SEC,
            check=False,
        )
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace")
            raise RuntimeError(f"Piper 失敗 word={word!r} rc={proc.returncode}: {stderr}")
        if not out_path.exists():
            raise RuntimeError(f"Piper 沒產出檔：{word!r}")
        return out_path.read_bytes()


async def _publish(word: str, data: bytes, content_type: str) -> str | None:
    """上傳 R2 或本地，產 audio_url。R2 全部失敗時保守 fallback 到本地。"""
    settings = get_settings()
    r2_key = f"audio/dict/{word}.wav"

    if settings.r2_endpoint and settings.r2_access_key_id:
        try:
            from shared.storage import r2

            r2.put_object(r2_key, data, content_type)
            return r2.presigned_get_url(r2_key)
        except StorageError as exc:
            logger.warning("R2 上傳失敗 word=%s: %s，改寫本地", word, exc)

    # 本地 fallback
    if settings.local_media_dir:
        out_dir = Path(settings.local_media_dir) / "dict"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{word}.wav"
        out_path.write_bytes(data)
        return f"{settings.public_base_url}/media/dict/{word}.wav"

    return None


async def _backfill_one(word: str, model: str) -> str | None:
    try:
        data = _synthesize(word, model)
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("Piper 合成失敗 word=%s: %s", word, exc)
        return None
    return await _publish(word, data, "audio/wav")


async def _process_batch(limit: int, model: str) -> tuple[int, int]:
    """回傳 (處理筆數, audio_url 寫入成功筆數)。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """select word from public.dict_cache
               where audio_url is null and word ~ '^[a-z]+$'
               order by created_at limit %s""",
            (limit,),
        )
        words = [r["word"] for r in await cur.fetchall()]

    if not words:
        return 0, 0

    ok = 0
    for word in words:
        url = await _backfill_one(word, model)
        if url is None:
            continue
        async with connection() as conn, conn.cursor() as cur:
            await cur.execute(
                "update public.dict_cache set audio_url = %s where word = %s",
                (url, word),
            )
        ok += 1
    return len(words), ok


def _resolve_model() -> str:
    """Piper 模型路徑：env PIPER_VOICE_MODEL > settings.piper_voice_model。

    預設搜 ~/.local/share/piper/en_US-amy-medium.onnx。
    """
    import os

    settings = get_settings()
    return (
        os.environ.get("PIPER_VOICE_MODEL")
        or getattr(settings, "piper_voice_model", "")
        or str(Path.home() / ".local/share/piper/en_US-amy-medium.onnx")
    )


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=int, default=500, help="本批處理字數上限（預設 500）")
    p.add_argument("--model", default=None, help="Piper ONNX 模型路徑；不傳則走 _resolve_model()")
    a = p.parse_args()

    model = a.model or _resolve_model()

    async def runner() -> tuple[int, int]:
        try:
            return await _process_batch(a.limit, model)
        finally:
            await close_pool()

    if not _piper_path():
        sys.exit("找不到 piper；先 `pip install piper-tts` 並下載 voice model")
    if not Path(model).exists():
        sys.exit(f"Piper 模型不存在：{model}")

    logger.info("開始 Piper backfill：limit=%d model=%s", a.limit, model)
    processed, ok = asyncio.run(runner())
    logger.info("完成：處理 %d，成功寫 audio_url %d", processed, ok)


if __name__ == "__main__":
    _amain()
