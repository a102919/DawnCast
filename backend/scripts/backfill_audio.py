"""⚠️ DEPRECATED：方案 B 上線後前端不再吃 dict_cache.audio_url。

播放路徑改走 player.playSegment 從該行 AudioBuffer 抽樣播（見
frontend/src/components/wordcard/PronounceButton.tsx 與
frontend/src/components/flashcard/ReplayAudioButton.tsx），
dict_cache.audio_url 變成 dead column。

此 script 保留 import 路徑以防誤刪破壞舊 deploy；若仍有線上客戶端吃到 audio_url
可暫跑補完；新部署請直接刪除此檔並執行 scripts/backfill_segments.py 處理
舊集音檔切段（見 plan/mighty-coalescing-wall.md Phase H）。

Piper 安裝（若仍要跑）：
  pip install piper-tts
  python -m piper.download_voices en_US-amy-medium
  # 模型預設路徑 ~/.local/share/piper/{voice}.onnx + .onnx.json

執行（後台跑；DEPRECATED）：
  uv run python -m scripts.backfill_audio --limit 500
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from engine.media.dict_audio import (
    list_words_missing_audio,
    shutdown_for_script,
    synthesize_word_audio,
    update_audio_url,
)

logger = logging.getLogger(__name__)


async def _process_batch(limit: int) -> tuple[int, int]:
    """回傳 (處理筆數, audio_url 寫入成功筆數)。"""
    words = await list_words_missing_audio(limit)
    if not words:
        return 0, 0
    ok = 0
    for word in words:
        url = await synthesize_word_audio(word)
        if url is None:
            continue
        await update_audio_url(word, url)
        ok += 1
    return len(words), ok


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=int, default=500, help="本批處理字數上限（預設 500）")
    a = p.parse_args()

    async def runner() -> tuple[int, int]:
        try:
            return await _process_batch(a.limit)
        finally:
            await shutdown_for_script()

    # piper 預先檢查：避免批跑到一半才發現沒裝。
    from engine.media.dict_audio import _piper_path, _resolve_model

    if not _piper_path():
        raise SystemExit("找不到 piper；先 `pip install piper-tts` 並下載 voice model")
    model = _resolve_model()
    if not Path(model).exists():
        raise SystemExit(f"Piper 模型不存在：{model}")

    logger.info("開始 Piper backfill：limit=%d model=%s", a.limit, model)
    processed, ok = asyncio.run(runner())
    logger.info("完成：處理 %d，成功寫 audio_url %d", processed, ok)


if __name__ == "__main__":
    _amain()