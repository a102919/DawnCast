"""補 dict_cache.example_en（來源：Free Dictionary API，dictionaryapi.dev，免 key）。

只 UPDATE 已有 translation（ECDICT/kaikki 灌進來的既有字）但缺 example_en 的列。
不新增字：新字交給 seed_dict_cache；translation 缺的字走 dict_translate LLM 佇列
（enqueue_missing_translations 的 WHERE 只看 translation，這條字不會被撈進去，
兩邊互不衝突）。查無例句的字保留 example_en = null，下一輪自然重試。

限速（官方文件）：
  同一 IP 每小時最多 1000 次請求，整點（UTC）重置；超過回 429，直到下個整點才解除。
本 script 把當前 UTC 小時剩餘配額平均攤到剩餘秒數（穩定發送、不搶跑），
配額用完或真的撞到 429 → 睡到下個整點再繼續。長駐迴圈，SIGINT/SIGTERM 收到後
跑完當筆就停（不會半途丟資料）。

執行（建議比照 dict_translate worker，用 launchd 常駐）：
  uv run python -m scripts.backfill_examples_freedictionary
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import signal

import httpx

from shared.config import get_settings
from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)

_HOURLY_LIMIT = 1000
_MIN_INTERVAL_SEC = 0.5  # 保底間隔，配額很寬鬆時也不瞬間打爆
_DB_BATCH = 200  # 每輪從 DB 撈幾個候選字
_IDLE_SLEEP_SEC = 600  # 沒有候選字時的休眠秒數
_API_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"

_stop = False


def _request_stop(*_args: object) -> None:
    global _stop
    _stop = True


def _seconds_to_next_utc_hour(now: dt.datetime) -> float:
    next_hour = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    return (next_hour - now).total_seconds()


class _RateLimited(Exception):
    """撞到 429，呼叫端把本小時配額視為用完。"""


async def _fetch_example(client: httpx.AsyncClient, word: str) -> str | None:
    """查一個字的英文例句；查無定義／查無例句回 None，撞 429 拋 _RateLimited。"""
    resp = await client.get(_API_URL.format(word=word))
    if resp.status_code == 429:
        raise _RateLimited
    if resp.status_code != 200:
        return None
    data = resp.json()
    if isinstance(data, dict):  # {"title": "No Definitions Found", ...}
        return None
    for entry in data:
        for meaning in entry.get("meanings", []):
            for definition in meaning.get("definitions", []):
                example = definition.get("example")
                if example:
                    return example.strip()
    return None


async def _next_batch(limit: int) -> list[str]:
    async with connection() as conn, conn.cursor() as cur:
        # ponytail: 排除 kaikki_stage 已覆蓋的字——那些字交給
        # dict-kaikki-translate-backfill workflow 處理（免費、順便補 zh），
        # 這裡不用浪費 API quota 去查重複的字。
        await cur.execute(
            """select d.word from public.dict_cache d
               where d.example_en is null
                 and d.translation <> ''
                 and d.word ~ '^[a-z]+$'
                 and not exists (select 1 from public.kaikki_stage k where k.word = d.word)
               order by d.created_at limit %s""",
            (limit,),
        )
        return [r["word"] for r in await cur.fetchall()]


async def _save_example(word: str, example: str) -> None:
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "update public.dict_cache set example_en = %s where word = %s and example_en is null",
            (example, word),
        )


async def run() -> None:
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    settings = get_settings()
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=settings.http_read_timeout,
        write=settings.http_read_timeout,
        pool=settings.http_connect_timeout,
    )

    used_this_hour = 0
    hour_key = dt.datetime.now(dt.UTC).hour

    try:
        headers = {"User-Agent": "DawnCast-dict-backfill/1"}
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            while not _stop:
                now = dt.datetime.now(dt.UTC)
                if now.hour != hour_key:
                    hour_key, used_this_hour = now.hour, 0

                remaining_budget = _HOURLY_LIMIT - used_this_hour
                if remaining_budget <= 0:
                    sleep_sec = _seconds_to_next_utc_hour(now)
                    logger.info("本小時配額用完，睡 %.0f 秒到下個整點", sleep_sec)
                    await asyncio.sleep(min(sleep_sec, _IDLE_SLEEP_SEC))
                    continue

                words = await _next_batch(min(_DB_BATCH, remaining_budget))
                if not words:
                    logger.info("目前沒有缺 example_en 的候選字，睡 %d 秒後重查", _IDLE_SLEEP_SEC)
                    await asyncio.sleep(_IDLE_SLEEP_SEC)
                    continue

                interval = max(_MIN_INTERVAL_SEC, _seconds_to_next_utc_hour(now) / remaining_budget)
                filled = 0
                for word in words:
                    if _stop:
                        break
                    try:
                        example = await _fetch_example(client, word)
                    except _RateLimited:
                        logger.warning("撞到 429，本小時配額提前用完")
                        used_this_hour = _HOURLY_LIMIT
                        break
                    except httpx.HTTPError as exc:
                        logger.warning("查詢失敗 word=%s: %s", word, exc)
                        used_this_hour += 1
                        await asyncio.sleep(interval)
                        continue
                    used_this_hour += 1
                    if example:
                        await _save_example(word, example)
                        filled += 1
                    await asyncio.sleep(interval)
                logger.info(
                    "本輪處理 %d 字，補上 example_en %d 字，本小時已用 %d/%d",
                    len(words),
                    filled,
                    used_this_hour,
                    _HOURLY_LIMIT,
                )
    finally:
        await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())
