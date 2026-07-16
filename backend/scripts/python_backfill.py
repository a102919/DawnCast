#!/usr/bin/env python3
"""Python-based Sharded Parallel Backfill Script.

Runs 5 parallel worker tasks to fetch, translate, and backfill dictionary cache example sentences
directly using the MiniMax Anthropic-compatible API.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import httpx
from typing import Any

# Add backend directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.config import get_settings
from shared.db.pool import close_pool, connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("python_backfill")

# Global semaphore to limit concurrent LLM API requests
API_SEMAPHORE = asyncio.Semaphore(15)


def build_fetch_sql(workers: int, worker_id: int, nonce: str, limit: int, offset: int) -> str:
    nonce_literal = nonce.replace("'", "''")
    return f"""
        select d.word, coalesce(nullif(d.example_en, ''), k.example_en) as example_en
        from dict_cache d left join kaikki_stage k on k.word = d.word
        where (d.example_zh is null or d.example_zh = '')
          and abs(hashtext(d.word)) % {workers} = {worker_id}
          and coalesce(nullif(d.example_en, ''), k.example_en) is not null
        order by d.frq asc nulls last, abs(hashtext(d.word || '{nonce_literal}'))
        limit {limit} offset {offset}
    """


async def fetch_batch(workers: int, worker_id: int, nonce: str, offset: int, limit: int) -> list[dict[str, str]]:
    sql = build_fetch_sql(workers, worker_id, nonce, limit, offset)
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(sql)
        rows = await cur.fetchall()
        return [{"word": r["word"], "example_en": r["example_en"]} for r in rows if r["word"] and r["example_en"]]


def parse_translation_response(text: str) -> list[dict[str, str]]:
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, list):
            return obj
    except Exception:
        try:
            start = s.find("[")
            end = s.rfind("]")
            if start >= 0 and end > start:
                obj = json.loads(s[start:end+1])
                if isinstance(obj, list):
                    return obj
        except Exception:
            pass
    return []


async def translate_batch(client: httpx.AsyncClient, settings: Any, pairs: list[dict[str, str]]) -> list[dict[str, str]]:
    input_lines = "\n".join(f"{p['word']}\t{p['example_en']}" for p in pairs)
    prompt = (
        "你是英文例句翻譯助手。以下是幾筆「英文單字＋真實英文例句」，例句已經是正確的真實語料，"
        "**不要改寫、不要重新生成例句**，只需要把每筆的 example_en 翻譯成台灣繁體中文（禁止大陸用詞，例如要寫「網路」「滑鼠」不是「网络」「鼠标」）。\n\n"
        "待翻譯清單（格式為 word 與 example_en，由 tab 分隔）：\n"
        f"{input_lines}\n\n"
        "請回傳一個 JSON 陣列，裡面包含每個單字的繁體中文翻譯，格式如下：\n"
        "[\n"
        "  {\n"
        '    "word": "英文單字",\n'
        '    "example_zh": "台灣繁體中文翻譯"\n'
        "  },\n"
        "  ...\n"
        "]\n"
        "請嚴格只輸出該 JSON 陣列，不要包含任何額外的 Markdown code block、說明文字或標題。"
    )

    payload = {
        "model": "abab6.5g",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": settings.minimax_auth_token,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    url = f"{settings.minimax_anthropic_base_url.rstrip('/')}/v1/messages"

    async with API_SEMAPHORE:
        for attempt in range(3):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"MiniMax API returned status {resp.status_code}, attempt {attempt+1}/3")
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                
                body = resp.json()
                text = "".join(
                    blk.get("text", "") for blk in body.get("content", []) if blk.get("type") == "text"
                )
                if not text:
                    logger.warning(f"Response content text is empty on attempt {attempt+1}/3. Raw body: {str(body)[:1000]}")
                parsed = parse_translation_response(text)
                if parsed:
                    # Map result to input pairs
                    zh_map = {item["word"]: item["example_zh"] for item in parsed if "word" in item and "example_zh" in item}
                    results = []
                    for p in pairs:
                        zh = zh_map.get(p["word"])
                        if zh:
                            results.append({"word": p["word"], "example_en": p["example_en"], "example_zh": zh})
                    return results
                
                logger.warning(f"Failed to parse JSON response on attempt {attempt+1}/3: text={text[:200]}, body={str(body)[:200]}")
            except Exception as e:
                logger.warning(f"Error calling MiniMax API on attempt {attempt+1}/3: {type(e).__name__}: {e}")
            await asyncio.sleep(2 * (attempt + 1))
    
    return []


def build_sql_body(entries: list[dict[str, str]]) -> str:
    rows = []
    for e in entries:
        w = e["word"].replace("'", "''")
        en = e["example_en"].replace("'", "''")
        zh = e["example_zh"].replace("'", "''")
        rows.append(f"('{w}','{en}','{zh}')")
    rows_str = ",\n  ".join(rows)
    return (
        "update dict_cache as d set\n"
        "  example_en = v.example_en,\n"
        "  example_zh = v.example_zh\n"
        "from (values\n"
        f"  {rows_str}\n"
        ") as v(word, example_en, example_zh)\n"
        "where d.word = v.word\n"
        "  and (d.example_zh is null or d.example_zh = '');"
    )


async def execute_db_update(entries: list[dict[str, str]]) -> int:
    query = """
        update public.dict_cache 
        set example_en = %s, example_zh = %s 
        where word = %s 
          and (example_zh is null or example_zh = '')
    """
    params = [(e["example_en"], e["example_zh"], e["word"]) for e in entries]
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute("select count(*) as count from public.dict_cache where example_zh is not null and example_zh <> '';")
        row = await cur.fetchone()
        before_count = row["count"] if row else 0
        
        await cur.executemany(query, params)
        
        await cur.execute("select count(*) as count from public.dict_cache where example_zh is not null and example_zh <> '';")
        row_after = await cur.fetchone()
        after_count = row_after["count"] if row_after else 0
        
        updated_rows = after_count - before_count
        return updated_rows


async def run_worker(worker_id: int, total_workers: int, target: int, chunk_size: int, write_db: bool, nonce: str) -> dict[str, Any]:
    worker_logger = logging.getLogger(f"Worker-{worker_id}")
    worker_logger.info(f"Worker {worker_id}/{total_workers} started with nonce {nonce}")

    # Fetch missing translations
    worker_logger.info("Fetching missing words...")
    fetch_batch_size = 250
    all_pairs = []
    for offset in range(0, target, fetch_batch_size):
        batch = await fetch_batch(total_workers, worker_id, nonce, offset, min(fetch_batch_size, target - len(all_pairs)))
        all_pairs.extend(batch)
        if len(batch) < fetch_batch_size:
            break

    # De-duplicate
    seen = set()
    unique_pairs = []
    for p in all_pairs:
        if p["word"] not in seen:
            seen.add(p["word"])
            unique_pairs.append(p)
    
    worker_logger.info(f"Fetched {len(unique_pairs)} unique words for translation.")
    if not unique_pairs:
        worker_logger.info("No candidates for translation. Stopping.")
        return {"worker_id": worker_id, "fetched": 0, "translated": 0, "written": 0}

    # Slice into chunks for translation
    chunks = [unique_pairs[i:i + chunk_size] for i in range(0, len(unique_pairs), chunk_size)]
    worker_logger.info(f"Split into {len(chunks)} chunks of size {chunk_size}")

    settings = get_settings()
    timeout = httpx.Timeout(
        connect=settings.http_connect_timeout,
        read=300.0,
        write=300.0,
        pool=settings.http_connect_timeout,
    )
    
    all_translated = []
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        # We can translate chunks concurrently
        tasks = [translate_batch(client, settings, chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks)
        for chunk_res in results:
            all_translated.extend(chunk_res)
            
    worker_logger.info(f"Translated {len(all_translated)} words successfully.")
    
    # Save debug files
    out_json = f"/tmp/dict_kaikki_examples_{nonce}.json"
    out_sql = f"/tmp/dict_kaikki_apply_{nonce}.sql"
    
    # Save debug JSON if within limits
    if len(all_translated) <= 500:
        try:
            with open(out_json, "w", encoding="utf-8") as f:
                json.dump(all_translated, f, indent=2, ensure_ascii=False)
            worker_logger.info(f"Saved debug JSON to {out_json}")
        except Exception as e:
            worker_logger.warning(f"Failed to write debug JSON: {e}")
            
    # Write and Verify SQL parts
    sql_chunks = [all_translated[i:i + 250] for i in range(0, len(all_translated), 250)]
    sql_part_paths = []
    for idx, sql_chunk in enumerate(sql_chunks):
        part_path = f"{out_sql}.p{idx}"
        try:
            sql_body = build_sql_body(sql_chunk)
            with open(part_path, "w", encoding="utf-8") as f:
                f.write(sql_body)
            sql_part_paths.append(part_path)
            worker_logger.info(f"Saved SQL part to {part_path}")
        except Exception as e:
            worker_logger.warning(f"Failed to write SQL part: {e}")

    written_count = 0
    if write_db and all_translated:
        worker_logger.info("Applying updates to database...")
        written_count = await execute_db_update(all_translated)
        worker_logger.info(f"Successfully updated {written_count} rows in DB.")
        
    return {
        "worker_id": worker_id,
        "fetched": len(unique_pairs),
        "translated": len(all_translated),
        "written": written_count,
        "sql_parts": sql_part_paths,
        "json_out": out_json if len(all_translated) <= 500 else None
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Python Sharded Parallel Backfill")
    parser.add_argument("--workers", type=int, default=5, help="Total number of sharding workers")
    parser.add_argument("--n", type=int, default=1500, help="Target words to process per worker")
    parser.add_argument("--chunk", type=int, default=50, help="Chunk size for translation batches")
    parser.add_argument("--write_db", type=bool, default=True, help="Whether to apply changes to database")
    args = parser.parse_args()

    logger.info(f"Starting {args.workers} parallel workers (n={args.n}, chunk={args.chunk}, write_db={args.write_db})")
    
    start_time = time.time()
    
    # Run workers in parallel
    worker_tasks = []
    for i in range(args.workers):
        nonce = f"{int(time.time())}_w{i+1}"
        worker_tasks.append(
            run_worker(
                worker_id=i,
                total_workers=args.workers,
                target=args.n,
                chunk_size=args.chunk,
                write_db=args.write_db,
                nonce=nonce
            )
        )
        # Stagger start slightly
        await asyncio.sleep(1)

    results = await asyncio.gather(*worker_tasks)
    
    duration = time.time() - start_time
    logger.info(f"All workers finished in {duration:.1f}s!")
    
    # Print summary
    print("\n" + "="*50)
    print("BACKFILL TRANSLATION SUMMARY")
    print("="*50)
    total_fetched = 0
    total_translated = 0
    total_written = 0
    for res in results:
        print(f"Worker {res['worker_id']}: fetched={res['fetched']}, translated={res['translated']}, written={res['written']}")
        total_fetched += res['fetched']
        total_translated += res['translated']
        total_written += res['written']
    print("-"*50)
    print(f"TOTAL: fetched={total_fetched}, translated={total_translated}, written={total_written}")
    print(f"Duration: {duration/60:.1f} minutes")
    print("="*50 + "\n")
    
    await close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")
