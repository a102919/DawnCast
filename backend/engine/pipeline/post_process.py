"""後處理（PRD §7.5）：generate_job 完成後補缺字到 dict_cache。

新集上架後，`target_vocab` 內的字可能尚未在 dict_cache（罕見：seed 灌了 760k）。
- 已有 → 跳過
- 缺 → enqueue `dict_translate` queue（worker 用 MiniMax 翻譯後 upsert）

best-effort：失敗不擋 generate，只記 log。generate_job 用 try/except 包住整段。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from shared.db.pool import connection
from shared.db.queue import send
from shared.models import TargetVocab

logger = logging.getLogger(__name__)

DICT_TRANSLATE_QUEUE = "dict_translate"


async def backfill_dict(target_vocab: Iterable[TargetVocab]) -> int:
    """回傳丟進 queue 的字數；已存在 dict_cache 的字跳過。"""
    words = list({v.word.casefold() for v in target_vocab if v.word.strip()})
    if not words:
        return 0
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "select word from public.dict_cache where word = any(%s)",
            (words,),
        )
        existing = {r[0] for r in await cur.fetchall()}
    missing = [w for w in words if w not in existing]
    for word in missing:
        await send(DICT_TRANSLATE_QUEUE, {"word": word})
    if missing:
        logger.info(
            "backfill_dict: enqueue %d/%d 字翻譯任務",
            len(missing),
            len(words),
        )
    return len(missing)
