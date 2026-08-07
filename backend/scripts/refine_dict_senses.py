"""把 dict_cache 的 ECDICT 原始 dump 精煉成結構化義項（migration 0030 / 0031）。

ECDICT 的 translation 是整坨字典釋義（run 有 30 個義項擠在一個欄位、用字面 \\n
分隔），前端怎麼排版都救不了。這支腳本分批餵給 MiniMax 重寫。

分成三個 job，各跑各的（0031 加的 stages bitmask 記進度）：

  --job refine    ECDICT dump → senses / core_sense / 對齊 senses[0] 的例句  (stage 1)
  --job mnemonic  依門檻補諧音提示，多數字會維持 null                        (stage 2)
  --job review    多義字的 core_sense 複審，硬湊的就審掉                     (stage 4)

為什麼不合成一次呼叫：三者對 LLM 的要求互相矛盾。義項/例句要收斂（低溫、格式嚴格），
諧音要發散（高溫、每個字都不一樣），複審是判斷題。0030 版塞在一起跑，諧音欄位塌成
單一模板（1869 筆裡 1387 筆開頭一模一樣），連 the / of / and 都生了一條。

tier 決定「先做哪些字」（只有 --job refine 用得到，另外兩個 job 吃已精煉的資料）：

  --tier vocab   使用者單字本裡的 lemma（最高優先，人真的在背這些字）
  --tier target  各集 episodes.target_vocab 選定的字（下一批會被點到的）
  --tier moe     教育部國中小參考字彙表 2040 字（migration 0033，會考範圍）
  --tier ceec    大考中心高中英文參考詞彙表 6115 字（migration 0032，學測/分科範圍）
  --tier freq    高頻字（--max-frq 控制範圍，預設 5000）

不管哪個 tier，取字都照課綱級別 → frq 排（_ORDER）：語料庫詞頻不等於台灣學生
要背的東西，大考第六級有 984 個字的 frq > 5000，照 frq 排永遠輪不到。

每輪只取還沒跑過該 stage 的字，寫回後就不再入選，所以重跑天然冪等、中斷可續跑。
--loop 會一直跑到沒有待處理的字為止。

執行：
    uv run python -m scripts.refine_dict_senses --job refine --tier vocab --loop
    uv run python -m scripts.refine_dict_senses --job mnemonic --loop
    uv run python -m scripts.refine_dict_senses --job review --loop
    uv run python -m scripts.refine_dict_senses --job refine --tier vocab --dry-run

限速：一次 CHUNK 字。--pause 是每批之間的休息秒數，撞 429 就調大
（dict_cache backfill 的歷史教訓：n=400/15min 一小時燒 48M tokens，1~2 天必撞）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

from engine.llm.translate import mnemonic_batch, refine_batch, review_core_senses
from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)

# 一次餵 25 個字：dict_cache backfill 實測的平衡點（再大 LLM 開始漏字、輸出被截斷）。
CHUNK = 25

# 0031 的 stages bitmask。
STAGE_REFINE = 1
STAGE_MNEMONIC = 2
STAGE_REVIEW = 4

# 短字不值得諧音拐杖，這也正是 0030 版產出最多廢話的地方（the / of / and）。
# 跟 translate.mnemonic_ok 的 _MIN_MNEMONIC_WORD_LEN 對齊；先在 SQL 濾掉省 LLM 額度。
MIN_MNEMONIC_WORD_LEN = 6

# tier → SQL where 片段。共通條件在 _fetch_refine 裡加。
_TIERS: dict[str, str] = {
    "vocab": """
        and d.word in (select distinct lemma from public.user_vocab)
    """,
    "target": """
        and d.word in (
            select distinct lower(jsonb_array_elements_text(e.target_vocab))
            from public.episodes e
            where e.target_vocab is not null
        )
    """,
    "freq": """
        and d.frq is not null and d.frq <= %(max_frq)s
    """,
    "ceec": """
        and d.ceec_level is not null
    """,
    "moe": """
        and d.moe_level is not null
    """,
}

# 三個 job 共用的取字順序：課綱級別低的先做（學校先教到的），同級再照語料庫詞頻。
#
# frq 單獨當排序鍵會排錯重點——大考第六級有 984 個字的 frq > 5000，照 frq 排要等到
# 很後面才輪到，但那些正是高中生在背的字（migration 0032 的註解有完整數字）。
#
# least(...) 把兩份字表併成同一條階梯：教育部基本 1200（國中）跟大考第一級一起先做，
# 兩邊的第二級再一起做。兩表重疊很多但不是包含關係，取較低的那級才不會漏掉只出現在
# 其中一邊的字（教育部有 174 個字不在大考表內）。9 是「不在任何字表」的墊底值。
_ORDER = """
    order by least(coalesce(d.moe_level, 9), coalesce(d.ceec_level, 9)),
             coalesce(d.ceec_level, 9), d.frq asc nulls last, d.word
"""


async def _query(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())  # pool 的預設 row_factory 是 dict_row


async def _exec(sql: str, params: tuple[Any, ...] | dict[str, Any]) -> None:
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(sql, params)


# --- job: refine ---------------------------------------------------------------


async def _fetch_refine(tier: str, max_frq: int, limit: int) -> list[dict[str, str]]:
    """取一批待精煉的字。

    只挑 word ~ '^[a-z]+$'：dict_cache 混了片語、專名、髒 key，那些不值得花 LLM 額度。
    """
    rows = await _query(
        f"""
        select d.word, d.translation
        from public.dict_cache d
        where d.quality = 0
          and d.translation is not null and d.translation <> ''
          and d.word ~ '^[a-z]+$'
          {_TIERS[tier]}
        {_ORDER}
        limit %(limit)s
        """,
        {"max_frq": max_frq, "limit": limit},
    )
    return [{"word": r["word"], "translation": r["translation"]} for r in rows]


async def _run_refine(entries: list[dict[str, str]], *, dry_run: bool) -> int:
    refined = await refine_batch(entries)
    done = 0
    for word, payload in refined.items():
        if payload is None:
            logger.warning("精煉失敗，留給下一輪 word=%s", word)
            continue
        if dry_run:
            print(_preview(word, payload))  # noqa: T201 — dry-run 就是要給人看
        else:
            # example_* 用 coalesce(新值, 既有值)：LLM 這次沒生出例句時保留舊的。
            # senses / core_sense 直接覆蓋——它們是這次精煉的主產出。只有 quality = 0
            # 的列會進到這裡，不會蓋掉人工確認過的資料（quality = 2）。
            await _exec(
                """
                update public.dict_cache set
                    senses     = %s::jsonb,
                    core_sense = %s,
                    example_en = coalesce(%s, example_en),
                    example_zh = coalesce(%s, example_zh),
                    quality    = 1,
                    stages     = stages | %s
                where word = %s and quality = 0
                """,
                (
                    json.dumps(payload["senses"], ensure_ascii=False),
                    payload.get("core_sense"),
                    payload.get("example_en"),
                    payload.get("example_zh"),
                    STAGE_REFINE,
                    word,
                ),
            )
        done += 1
    return done


def _preview(word: str, payload: dict[str, Any]) -> str:
    senses = payload["senses"]
    assert isinstance(senses, list)
    head = " / ".join(f"{s.get('pos', '')}{s['zh']}" for s in senses)
    lines = [f"{word:<16} {head}"]
    if core := payload.get("core_sense"):
        lines.append(f"{'':<16} 💡 {core}")
    if payload.get("example_en"):
        lines.append(f"{'':<16} {payload['example_en']}  ／  {payload.get('example_zh', '')}")
    return "\n".join(lines)


# --- job: mnemonic -------------------------------------------------------------


async def _skip_short_words() -> int:
    """<6 字母的字直接標成 mnemonic 階段完成，不浪費 LLM 額度。

    回傳跳過的字數。這批字的 mnemonic 維持 null，卡片上就不會出現「記憶小提示」。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.dict_cache set stages = stages | %s
            where quality >= 1 and (stages & %s) = 0 and char_length(word) < %s
            """,
            (STAGE_MNEMONIC, STAGE_MNEMONIC, MIN_MNEMONIC_WORD_LEN),
        )
        return cur.rowcount


async def _fetch_mnemonic(limit: int) -> list[dict[str, str]]:
    """取一批待補諧音的字，附上 senses[0].zh 當語意錨點。"""
    rows = await _query(
        f"""
        select d.word, coalesce(d.senses -> 0 ->> 'zh', '') as zh
        from public.dict_cache d
        where d.quality >= 1 and (d.stages & %(stage)s) = 0
          and char_length(d.word) >= %(min_len)s
          and d.senses is not null
        {_ORDER}
        limit %(limit)s
        """,
        {"stage": STAGE_MNEMONIC, "min_len": MIN_MNEMONIC_WORD_LEN, "limit": limit},
    )
    return [{"word": r["word"], "zh": r["zh"]} for r in rows]


async def _run_mnemonic(entries: list[dict[str, str]], *, dry_run: bool) -> int:
    result = await mnemonic_batch(entries)
    for word, text in result.items():
        if dry_run:
            print(f"{word:<16} {text or '—'}")  # noqa: T201
            continue
        # text 是 None 代表「審過了，這個字不需要提示」——一樣要標 stage，
        # 否則 --loop 會永遠重撈同一批字。
        await _exec(
            """
            update public.dict_cache
            set mnemonic = %s, stages = stages | %s
            where word = %s and quality >= 1
            """,
            (text, STAGE_MNEMONIC, word),
        )
    return len(result)


# --- job: review ---------------------------------------------------------------


async def _skip_unreviewable() -> int:
    """沒有 core_sense 的字沒東西可審，直接標階段完成。回傳跳過的字數。"""
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            update public.dict_cache set stages = stages | %s
            where quality >= 1 and (stages & %s) = 0 and core_sense is null
            """,
            (STAGE_REVIEW, STAGE_REVIEW),
        )
        return cur.rowcount


async def _fetch_review(limit: int) -> list[dict[str, Any]]:
    rows = await _query(
        f"""
        select d.word, d.senses, d.core_sense
        from public.dict_cache d
        where d.quality = 1 and (d.stages & %(stage)s) = 0
          and d.core_sense is not null
        {_ORDER}
        limit %(limit)s
        """,
        {"stage": STAGE_REVIEW, "limit": limit},
    )
    return [
        {"word": r["word"], "senses": r["senses"], "core_sense": r["core_sense"]} for r in rows
    ]


async def _run_review(entries: list[dict[str, Any]], *, dry_run: bool) -> int:
    result = await review_core_senses(entries)
    before = {e["word"]: e["core_sense"] for e in entries}
    for word, core in result.items():
        if dry_run:
            mark = "＝" if core == before[word] else "→"
            print(f"{word:<16} {before[word]}  {mark}  {core or '（審掉）'}")  # noqa: T201
            continue
        await _exec(
            """
            update public.dict_cache
            set core_sense = %s, stages = stages | %s
            where word = %s and quality = 1
            """,
            (core, STAGE_REVIEW, word),
        )
    return len(result)


# --- 主迴圈 --------------------------------------------------------------------


async def _one_round(args: argparse.Namespace, size: int) -> tuple[int, int]:
    """跑一批，回傳 (取到幾個字, 成功處理幾個字)。取到 0 代表這個 job 做完了。"""
    if args.job == "refine":
        entries = await _fetch_refine(args.tier, args.max_frq, size)
        return len(entries), await _run_refine(entries, dry_run=args.dry_run)
    if args.job == "mnemonic":
        m_entries = await _fetch_mnemonic(size)
        return len(m_entries), await _run_mnemonic(m_entries, dry_run=args.dry_run)
    r_entries = await _fetch_review(size)
    return len(r_entries), await _run_review(r_entries, dry_run=args.dry_run)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--job", choices=("refine", "mnemonic", "review"), default="refine")
    ap.add_argument("--tier", choices=sorted(_TIERS), default="vocab", help="只有 --job refine 用")
    ap.add_argument("--max-frq", type=int, default=5000, help="--tier freq 的頻率上限")
    ap.add_argument("--limit", type=int, default=CHUNK, help="單輪取幾個字（0 = 不限，配 --loop）")
    ap.add_argument("--loop", action="store_true", help="一直跑到沒有待處理的字")
    ap.add_argument("--pause", type=float, default=2.0, help="每批之間休息秒數（撞 429 就調大）")
    ap.add_argument("--dry-run", action="store_true", help="只印結果不寫 DB")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    total = 0
    # 失敗的批次不會被標 stage，下一輪會撈到同一批字重試。偶發的輸出截斷重試一次
    # 就過了，不值得中斷整趟；但連續失敗多半是 429 或 API 掛了，再跑下去只是把額度
    # 燒在同 25 個字上，所以設連續上限。
    consecutive_failures = 0
    max_consecutive_failures = 3
    try:
        if not args.dry_run:
            if args.job == "mnemonic" and (n := await _skip_short_words()):
                logger.info("跳過 %d 個 <%d 字母的字（不需要諧音提示）", n, MIN_MNEMONIC_WORD_LEN)
            elif args.job == "review" and (n := await _skip_unreviewable()):
                logger.info("跳過 %d 個沒有 core_sense 的字", n)

        while True:
            size = CHUNK if args.limit == 0 else min(args.limit, CHUNK)
            fetched, done = await _one_round(args, size)
            if fetched == 0:
                logger.info("job=%s 沒有待處理的字了", args.job)
                break
            total += done
            logger.info("批次完成 %d/%d（累計 %d）", done, fetched, total)
            # dry-run 不寫 DB，下一輪會撈到同一批字 → 無窮迴圈，跑一輪就停。
            if args.dry_run or not args.loop:
                break
            if done == 0:
                consecutive_failures += 1
                if consecutive_failures >= max_consecutive_failures:
                    logger.error("連續 %d 批全數失敗，中止；重跑會從同一批字接續", consecutive_failures)
                    break
                logger.warning("整批失敗（第 %d 次），重試同一批", consecutive_failures)
            else:
                consecutive_failures = 0
            await asyncio.sleep(args.pause)
    finally:
        await close_pool()
    logger.info("結束，job=%s 共處理 %d 字", args.job, total)


if __name__ == "__main__":
    asyncio.run(main())
