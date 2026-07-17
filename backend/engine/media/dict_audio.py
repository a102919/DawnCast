"""單字 TTS + 發佈：dict_cache 音檔的單一 source of truth。

給兩條呼叫路徑共用：
  - scripts/backfill_audio.py（批次補檔）
  - app/routers/dict.py 的 lazy on-demand 路徑（首次查無音檔 inline 觸發）

公開 API：synthesize_word_audio(word) -> str | None
  - 守門 ^[a-z]+$：非單字（片語 / 數字 / 大寫）直接 None，避免 piper 對非單字合成失敗。
  - 任何例外（piper 沒裝 / R2 失敗 / publish 回 None）一律降級回 None，不外拋。
    上游 router 已經會把 None 視為「無音檔」並回 audioUrl=null，不會污染主流程。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from shared.config import get_settings
from shared.db.pool import close_pool, connection
from shared.errors import StorageError

logger = logging.getLogger(__name__)

_PIPER_TIMEOUT_SEC = 15  # 單字合成 <2s，留緩衝
_SINGLE_WORD = re.compile(r"^[a-z]+$")


def _piper_path() -> str:
    """piper 執行檔路徑（PATH 內或顯式 PIPER_BIN）。"""
    import os
    import shutil

    return os.environ.get("PIPER_BIN") or shutil.which("piper") or ""


def _synthesize(word: str, model: str) -> bytes:
    """subprocess 跑 Piper，回傳音檔 bytes。

    Piper 介面：piper --model <voice.onnx> --output_file <out.wav> < textfile_or_stdin
    為簡化，把 word 寫到 stdin（單行）。輸出 wav。
    """
    import subprocess
    import tempfile

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

    if settings.local_media_dir:
        out_dir = Path(settings.local_media_dir) / "dict"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{word}.wav"
        out_path.write_bytes(data)
        return f"{settings.public_base_url}/media/dict/{word}.wav"

    return None


def _resolve_model() -> str:
    """Piper 模型路徑：env PIPER_VOICE_MODEL > settings > 預設 ~/.local/share/piper。"""
    import os

    settings = get_settings()
    return (
        os.environ.get("PIPER_VOICE_MODEL")
        or settings.piper_voice_model
        or str(Path.home() / ".local/share/piper/en_US-amy-medium.onnx")
    )


async def synthesize_word_audio(word: str) -> str | None:
    """合成 + 發佈單字音檔，回 audio_url；任何環節失敗一律回 None。

    守門 ^[a-z]+$：非單字（片語、數字、大寫開頭）直接 None，
    避免 piper 對「hello world」之類輸入產出不可預期結果或直接報錯。

    合成走 asyncio.to_thread，避免 subprocess.run 阻塞 FastAPI event loop。
    """
    if not _SINGLE_WORD.match(word):
        return None
    try:
        data = await asyncio.to_thread(_synthesize, word, _resolve_model())
    except (RuntimeError, FileNotFoundError, TimeoutError, OSError) as exc:
        logger.warning("Piper 合成失敗 word=%s: %s", word, exc)
        return None
    url = await _publish(word, data, "audio/wav")
    if url is None:
        logger.warning("音檔發佈失敗 word=%s（R2 與本地皆無）", word)
    return url


# 重匯出讓 backfill 與測試能直接 patch 底層函式
__all__ = ["synthesize_word_audio", "_synthesize", "_publish"]


# 給 backfill 用的 async helper：跑完一輪後關 pool（避免 lingering connection）
# 由 script 端 `_amain()` 顯式呼叫，避免污染 router 路徑。
async def shutdown_for_script() -> None:  # pragma: no cover — CLI 路徑
    await close_pool()


# 給 backfill 用的 batch query：列出 audio_url is null 且是單字的 word。
async def list_words_missing_audio(limit: int) -> list[str]:
    from psycopg.rows import dict_row

    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """select word from public.dict_cache
               where audio_url is null and word ~ '^[a-z]+$'
               order by created_at limit %s""",
            (limit,),
        )
        rows = await cur.fetchall()
    return [r["word"] for r in rows]


async def update_audio_url(word: str, url: str) -> None:  # pragma: no cover — CLI 路徑
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.dict_cache set audio_url = %s where word = %s",
            (url, word),
        )