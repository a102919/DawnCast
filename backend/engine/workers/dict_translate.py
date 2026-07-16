"""dict_translate queue consumer：批次補缺字到 dict_cache。

消費 engine.pipeline.post_process.DICT_TRANSLATE_QUEUE = "dict_translate"。
每筆訊息：{"word": "<lowercase>"}
worker 流程：read_batch → translate_batch (MiniMax) → 逐筆 upsert dict_cache on conflict → 成功才 delete。

批次大小 BATCH_SIZE=40：一次 prompt 丟 40 字翻譯，比單字版 ~40x 加速
（單字版 MiniMax API round-trip ~25~50s/字；batch 版 latency 接近單字但 output token 對齊 N 筆）。

失敗策略（pgmq 內建 vt + read_ct）：
  - 整批 API 失敗 / JSON 解析失敗 → 所有字不 delete → vt 到期 pgmq 自動重投。
  - 批次內個別字 LLM 漏翻（payload=None）→ 不 delete → 同上重投。
  - read_ct 超過 dead_letter_after → archive（手動重建 / 觀測）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from engine.llm.translate import translate_batch
from engine.pipeline.post_process import DICT_TRANSLATE_QUEUE
from shared.config import get_settings
from shared.db.pool import close_pool, connection
from shared.db.queue import Msg, archive, delete, read_batch

logger = logging.getLogger(__name__)

_VT_SEC = 240  # 10 字冷僻字實測 ~80s（thinking 6K tokens），vt 拉到 240 給 LLM 完成輸出
_BATCH_SIZE = 10


async def _upsert(word: str, payload: dict[str, Any]) -> bool:
    """從翻譯結果 upsert。任一欄位缺就只填存在的（on conflict do update 全 coalesce）。"""
    translation = payload.get("translation")
    if not translation:
        return False
    ipa = payload.get("ipa")
    pos = payload.get("pos") or []
    example_en = payload.get("example_en")
    example_zh = payload.get("example_zh")
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.dict_cache (word, ipa, pos, translation, example_en, example_zh)
            values (%s, %s, %s::jsonb, %s, %s, %s)
            on conflict (word) do update set
                ipa = coalesce(excluded.ipa, public.dict_cache.ipa),
                translation = case
                    when public.dict_cache.translation = ''
                    then excluded.translation
                    else public.dict_cache.translation
                end,
                example_en = coalesce(excluded.example_en, public.dict_cache.example_en),
                example_zh = coalesce(excluded.example_zh, public.dict_cache.example_zh)
            """,
            (word, ipa, json.dumps(pos, ensure_ascii=False), translation, example_en, example_zh),
        )
    return True


async def _handle_batch(msgs: list[Msg]) -> None:
    words = [(m.body.get("word") or "").strip().casefold() for m in msgs]
    # 空 word 直接 archive（poison pill），不浪費 LLM token
    valid: list[tuple[Msg, str]] = []
    for msg, word in zip(msgs, words, strict=True):
        if not word:
            await archive(DICT_TRANSLATE_QUEUE, msg.msg_id)
            continue
        valid.append((msg, word))

    if not valid:
        return

    word_list = [w for _, w in valid]
    results = await translate_batch(word_list)
    if not results:
        # 整批 LLM 失敗 → 全不 delete，vt 到期重投
        logger.warning("dict_translate batch translate 全失敗 n=%d", len(word_list))
        return

    settings = get_settings()
    dead_letter = settings.dead_letter_after
    archived = deleted = upserted = 0
    for msg, word in valid:
        payload = results.get(word)
        if payload is None:
            if msg.read_ct >= dead_letter:
                await archive(DICT_TRANSLATE_QUEUE, msg.msg_id)
                archived += 1
            continue
        if await _upsert(word, payload):
            await delete(DICT_TRANSLATE_QUEUE, msg.msg_id)
            deleted += 1
            upserted += 1
    logger.info(
        "dict_translate batch n=%d upserted=%d deleted=%d archived=%d",
        len(valid), upserted, deleted, archived,
    )


async def run() -> None:
    """worker 主迴圈：無任務時 sleep；read_batch 一次拿 BATCH_SIZE 筆一起翻譯。"""
    try:
        while True:
            try:
                msgs = await read_batch(DICT_TRANSLATE_QUEUE, vt=_VT_SEC, qty=_BATCH_SIZE)
            except Exception as exc:  # noqa: BLE001
                pause = get_settings().pause_sec
                logger.warning("dict_translate read 例外，%ds 後重試: %s", pause, exc)
                await asyncio.sleep(pause)
                continue
            if not msgs:
                await asyncio.sleep(get_settings().pause_sec)
                continue
            try:
                await _handle_batch(msgs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("dict_translate handler 例外 n=%d: %s", len(msgs), exc)
    finally:
        await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())