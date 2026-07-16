import os
import sys
import json
import argparse
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from shared.db.pool import connection, close_pool

async def fetch_words(limit: int):
    # Fetch words that do not have example_zh but have example_en
    sql = """
        select d.word, coalesce(nullif(d.example_en, ''), k.example_en) as example_en
        from dict_cache d left join kaikki_stage k on k.word = d.word
        where (d.example_zh is null or d.example_zh = '')
          and coalesce(nullif(d.example_en, ''), k.example_en) is not null
        order by d.frq asc nulls last
        limit %s
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, (limit,))
        rows = await cur.fetchall()
        res = [{"word": r["word"], "example_en": r["example_en"], "example_zh": ""} for r in rows if r["word"] and r["example_en"]]
        
        with open("/tmp/to_translate.json", "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)
        print(f"Successfully fetched {len(res)} untranslated sentences and wrote to /tmp/to_translate.json")
    await close_pool()

async def apply_translations():
    filepath = "/tmp/translations.txt"
    if not os.path.exists(filepath):
        print(f"Error: {filepath} does not exist.")
        return

    updates = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                updates.append((parts[1], parts[0]))

    if not updates:
        print("No translations found to update in the file.")
        return

    print(f"Applying {len(updates)} translations to the database...")
    async with connection() as conn, conn.cursor() as cur:
        sql = """
            update public.dict_cache 
            set example_zh = %s 
            where word = %s 
              and (example_zh is null or example_zh = '')
        """
        await cur.executemany(sql, updates)
        print(f"Done! Successfully updated {len(updates)} rows in the database.")
    await close_pool()

async def backfill_en_examples():
    sql = """
        update public.dict_cache d
        set example_en = k.example_en
        from public.kaikki_stage k
        where k.word = d.word
          and (d.example_en is null or d.example_en = '')
          and (d.example_zh is not null and d.example_zh <> '')
          and k.example_en is not null and k.example_en <> ''
    """
    print("Backfilling missing example_en from kaikki_stage...")
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(sql)
        print(f"Done! Successfully backfilled {cur.rowcount} rows in the database.")
    await close_pool()


def main():
    parser = argparse.ArgumentParser(description="Gemini Assistant Translation Helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # fetch command
    fetch_parser = subparsers.add_parser("fetch", help="Fetch untranslated words")
    fetch_parser.add_argument("--limit", type=int, default=500, help="Number of rows to fetch")

    # apply command
    subparsers.add_parser("apply", help="Apply translations from JSON file to DB")

    # backfill-en command
    subparsers.add_parser("backfill-en", help="Backfill missing example_en from kaikki_stage")

    args = parser.parse_args()

    if args.command == "fetch":
        asyncio.run(fetch_words(args.limit))
    elif args.command == "apply":
        asyncio.run(apply_translations())
    elif args.command == "backfill-en":
        asyncio.run(backfill_en_examples())

if __name__ == "__main__":
    main()

