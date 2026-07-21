"""把 dict_cache 裡缺 translation 的字丟進 dict_translate 佇列。

worker（engine.pipeline.dict_translate，主 worker 迴圈輪詢）會用 MiniMax 翻譯後 upsert。

排除 kaikki_stage 已覆蓋的字（kaikki 翻譯 workflow 已經接手這批字的 example_zh）。
如果不在這裡排除，enqueue 跟 kaikki workflow 會對同一批字重複加工。

執行模式：
  --loop   不斷掃直到 SELECT 結果為 0（worker 持續消化）
  --pause  每輪之間休息秒數（預設 0；建議配合 --loop 加 30~60 讓 worker 有喘氣空間）
  --limit  只跑一輪 N 字就 exit（不論有沒有 --loop）；0 表示全送（預設 0）

限速：worker 翻譯用 MiniMax，這邊只負責 enqueue（一次 pgmq.send ≈ 1ms）。
可重複執行：pgmq.send 接受重複 word，worker 用 upsert 冪等，第二輪會被 _upsert 視為已翻譯而 noop。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal

from engine.pipeline.post_process import DICT_TRANSLATE_QUEUE
from shared.db.pool import close_pool, connection
from shared.db.queue import send

logger = logging.getLogger(__name__)

_SELECT_SQL = """
select word from public.dict_cache
where (translation is null or translation = '')
  and word ~ '^[a-z]+$'
  and not exists (
    select 1 from public.kaikki_stage k where k.word = public.dict_cache.word
  )
order by frq asc nulls last, word
"""

_BATCH = 1000


async def _enqueue_round(limit: int) -> int:
    """掃一輪缺翻譯字並 enqueue。回傳本輪 enqueue 數。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(_SELECT_SQL)
        words = [r["word"] for r in await cur.fetchall()]
        if limit > 0:
            words = words[:limit]

    if not words:
        return 0

    sent = 0
    for i in range(0, len(words), _BATCH):
        chunk = words[i : i + _BATCH]
        for w in chunk:
            await send(DICT_TRANSLATE_QUEUE, {"word": w})
        sent += len(chunk)
        logger.info("enqueue 進度 %d/%d", sent, len(words))
    return sent


async def _amain(args: argparse.Namespace) -> None:
    total_sent = 0
    round = 0
    stop = asyncio.Event()

    def _on_sigint(*_: object) -> None:
        logger.info("收到 SIGINT，準備在下一輪結束後停...")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_sigint)

    try:
        while True:
            round += 1
            sent = await _enqueue_round(args.limit)
            total_sent += sent
            if sent == 0:
                logger.info("round %d: SELECT 結果為 0，DB 已無待 enqueue 的字，結束", round)
                break
            logger.info("round %d: 已 enqueue %d 字，累計 %d", round, sent, total_sent)
            if not args.loop:
                break
            if args.pause > 0 and not stop.is_set():
                logger.info("round %d: 休息 %ds...", round, args.pause)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=args.pause)
                except asyncio.TimeoutError:
                    pass
            if stop.is_set():
                break
    finally:
        await close_pool()

    logger.info("完成：總共 enqueue %d 字（%d 輪）", total_sent, round)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--limit", type=int, default=0, help="每輪最多送幾字；0 表示全送（預設 0）")
    p.add_argument("--loop", action="store_true", help="不斷掃直到 SELECT 為 0")
    p.add_argument("--pause", type=int, default=0, help="loop 模式下每輪之間休息秒數（預設 0）")
    args = p.parse_args()
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
