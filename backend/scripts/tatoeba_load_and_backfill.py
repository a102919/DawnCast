"""補 dict_cache.example_en（來源：Tatoeba 開放句庫，CC-BY 2.0 FR，免 key）。

只補「有 frq（ECDICT 收錄的常用字）但 example_en 為空」的列——這批字已確認
不在 kaikki_stage 裡（見 backfill_examples_freedictionary.py 的排除條件），
两邊互不重疊。

流程（純 SQL，離線，無外部 API 呼叫）：
  1. 讀本機解壓後的 sentences.csv（Tatoeba weekly export），過濾 lang == 'eng'
  2. 灌進暫存表 tatoeba_sentences(id, text)
  3. 拆字建反查索引 tatoeba_words(sentence_id, word) —— 一次建表，之後每個
     目標單字都是一次 index lookup，不必逐字發 API
  4. 對每個目標字，從候選句子中挑一句長度適中（20~120 字元）的當例句，
     UPDATE dict_cache.example_en

執行（一次性 backfill，非長駐 worker）：
  uv run python -m scripts.tatoeba_load_and_backfill --sentences-file /tmp/tatoeba/sentences.csv
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import logging
import re
import sys

from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)

_WORD_RE = re.compile(r"[a-z']+")
_MIN_LEN = 20
_MAX_LEN = 120


async def _ensure_staging_tables() -> None:
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            create table if not exists tatoeba_sentences (
                id bigint primary key,
                text text not null
            );
            create table if not exists tatoeba_words (
                sentence_id bigint not null references tatoeba_sentences(id),
                word text not null
            );
            create index if not exists tatoeba_words_word_idx on tatoeba_words (word);
            """
        )


async def _load_sentences(path: str) -> int:
    """讀 Tatoeba sentences.csv，只留英文列，批次 COPY 進 tatoeba_sentences。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute("select count(*) from tatoeba_sentences")
        existing = (await cur.fetchone())["count"]
        if existing > 0:
            logger.info("tatoeba_sentences 已有 %d 筆，略過載入", existing)
            return existing

        rows: list[tuple[int, str]] = []
        total = 0
        with open(path, encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
            for row in reader:
                if len(row) != 3:
                    continue
                sid, lang, text = row
                if lang != "eng":
                    continue
                rows.append((int(sid), text))
                if len(rows) >= 5000:
                    async with cur.copy(
                        "copy tatoeba_sentences (id, text) from stdin"
                    ) as copy:
                        for r in rows:
                            await copy.write_row(r)
                    total += len(rows)
                    rows = []
            if rows:
                async with cur.copy(
                    "copy tatoeba_sentences (id, text) from stdin"
                ) as copy:
                    for r in rows:
                        await copy.write_row(r)
                total += len(rows)
        logger.info("載入 %d 筆英文句子", total)
        return total


async def _build_word_index() -> None:
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute("select count(*) from tatoeba_words")
        if (await cur.fetchone())["count"] > 0:
            logger.info("tatoeba_words 索引已建，略過")
            return

        await cur.execute("select id, text from tatoeba_sentences")
        rows = await cur.fetchall()

    pairs: list[tuple[int, str]] = []
    for row in rows:
        sid, text = row["id"], row["text"]
        if not (_MIN_LEN <= len(text) <= _MAX_LEN):
            continue
        seen: set[str] = set()
        for m in _WORD_RE.finditer(text.lower()):
            w = m.group(0)
            if w in seen:
                continue
            seen.add(w)
            pairs.append((sid, w))

    async with connection() as conn, conn.cursor() as cur:
        for i in range(0, len(pairs), 5000):
            chunk = pairs[i : i + 5000]
            async with cur.copy(
                "copy tatoeba_words (sentence_id, word) from stdin"
            ) as copy:
                for r in chunk:
                    await copy.write_row(r)
    logger.info("建好反查索引，%d 個 (word, sentence) pair", len(pairs))


async def _backfill() -> int:
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select d.word
            from public.dict_cache d
            where d.frq is not null
              and (d.example_en is null or d.example_en = '')
              and d.word ~ '^[a-z]+$'
            """
        )
        targets = [r["word"] for r in await cur.fetchall()]
    logger.info("待補高頻字：%d", len(targets))

    filled = 0
    async with connection() as conn, conn.cursor() as cur:
        for word in targets:
            await cur.execute(
                """
                select s.text
                from tatoeba_words w
                join tatoeba_sentences s on s.id = w.sentence_id
                where w.word = %s
                order by length(s.text) asc
                limit 1
                """,
                (word,),
            )
            row = await cur.fetchone()
            if row is None:
                continue
            await cur.execute(
                "update public.dict_cache set example_en = %s "
                "where word = %s and (example_en is null or example_en = '')",
                (row["text"], word),
            )
            filled += 1
            if filled % 500 == 0:
                logger.info("已補 %d / %d", filled, len(targets))
    return filled


async def run(sentences_file: str) -> None:
    await _ensure_staging_tables()
    await _load_sentences(sentences_file)
    await _build_word_index()
    filled = await _backfill()
    logger.info("完成，補上 example_en %d 字", filled)
    await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--sentences-file", required=True)
    args = parser.parse_args()
    asyncio.run(run(args.sentences_file))
