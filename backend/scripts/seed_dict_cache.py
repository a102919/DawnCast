"""灌 dict_cache 表（Layer 0，一次性 backfill）。

來源（手動下載好再跑，script 不替你 wget）：
  --ecdict-csv     ECDICT CSV（github.com/skywind3000/ECDICT）
  --kaikki-jsonl   kaikki.org-dictionary-English.jsonl（3GB，建議給；補 IPA 與例句）

授權：
  - ECDICT  MIT              — 保留 LICENSE（data/dict-backfill/LICENSE-ecdict）。
  - kaikki  CC BY-SA 4.0     — 純 app 內部用（dict_cache 不對外 export），不觸發 share-alike。

兩個來源都跑同一條 UPSERT（缺項補，冪等可重跑）：
  - kaikki 先（rich IPA，但翻譯可能是簡體）
  - ECDICT 後（curated pos + zh 翻譯，補缺）

OpenCC s2twp：簡體→繁體台灣（網路/網路、磁碟/磁碟、滑鼠/滑鼠）。

執行：
  uv run python -m scripts.seed_dict_cache \
      --ecdict-csv  /tmp/ecdict.csv \
      --kaikki-jsonl /tmp/kaikki-org-dictionary-English.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)

# pos 收斂：ECDICT 的 pos 欄位夾雜 '/n' 'v.' 'n.&v.' 等雜訊，只留標準詞性標。
_VALID_POS = {
    "n",
    "v",
    "vi",
    "vt",
    "adj",
    "adv",
    "prep",
    "conj",
    "art",
    "pron",
    "int",
    "num",
    "aux",
    "det",
    # 衍生／罕見
    "interj",  # interjection
    "pref",
    "suf",
    "comb",
    "abbr",  # prefix/suffix/combining form/abbreviation
}
# 中文圈常用縮寫 → 標準詞性
_POS_ALIAS = {"a": "adj"}

# OpenCC 簡→台繁；沒裝就退化（translation 原樣寫入，但會有簡體殘留）。
try:
    import opencc

    _CONVERTER: opencc.OpenCC | None = opencc.OpenCC("s2twp")
except ImportError:  # pragma: no cover — opt-in dep
    _CONVERTER = None
    logger.warning("opencc 未安裝，跳過簡→台繁轉換，翻譯會有簡體殘留")


# ── pos/zh 標準化 ─────────────────────────────────────


def _normalize_pos(raw: str | None) -> list[str]:
    """從 'n./v.' 'n.&v.' '/n' 等雜訊字串抽有效詞性，回傳 list[str]。"""
    if not raw:
        return []
    cleaned = raw.replace("/", " ").replace(",", " ").replace("&", " ")
    out: list[str] = []
    seen: set[str] = set()
    for tag in cleaned.split():
        tag = tag.strip(". ").lower()
        tag = _POS_ALIAS.get(tag, tag)
        if tag in _VALID_POS and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _extract_pos_from_translation(text: str) -> tuple[list[str], str]:
    """從 'n. 電腦, ...' 開頭抽 pos，剝前綴後回剩餘字串。沒合法前綴 → 不動。

    只看第一段（遇 `\\n` 或 `[xxx]` 領域標記停止）：
      - 第一個空格之前的 token 若屬合法 pos → 取出，剝掉並清理尾端標點
      - 否則 fallback 視作無 prefix，回傳 ([], text)
    """
    first_line, _, rest = text.partition("\n")
    first_line = first_line.strip()
    space = first_line.find(" ")
    if space <= 0:
        return [], text
    prefix = first_line[:space]
    pos = _normalize_pos(prefix)
    if not pos:
        return [], text
    after = first_line[space:].lstrip(" ,;。")
    cleaned = ((after + "\n" + rest) if rest else after).strip()
    return pos, (cleaned or text)


def _convert_zh(text: str | None) -> str:
    if not text:
        return ""
    if _CONVERTER is None:
        return text
    return _CONVERTER.convert(text)


# ── 來源 iter ──────────────────────────────────────────


def _iter_ecdict(path: Path) -> Iterator[tuple[str, str | None, str, list[str], str | None]]:
    """ECDICT CSV → (word, ipa, translation, pos, exchange)。

    POS 兩路徑：先用 ECDICT 獨立 pos 欄位（通常空），fallback 從 translation 開頭抽。
    抽到時同步剝離前綴，避免 translation 殘留 'n. 電腦' 這種雙重訊息。
    """
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            word = (row.get("word") or "").strip().casefold()
            if not word:
                continue
            raw_translation = _convert_zh((row.get("translation") or "").strip())
            explicit_pos = _normalize_pos(row.get("pos"))
            extracted_pos, cleaned_translation = _extract_pos_from_translation(raw_translation)
            pos_list = explicit_pos if explicit_pos else extracted_pos
            yield (
                word,
                (row.get("phonetic") or "").strip() or None,
                cleaned_translation,
                pos_list,
                (row.get("exchange") or "").strip() or None,
            )


def _iter_kaikki(path: Path) -> Iterator[tuple[str, str | None, str, list[str], str | None]]:
    """kaikki JSONL → 同上 tuple。檔案 3GB，採逐行 stream parse。"""
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            word = (obj.get("word") or "").casefold()
            if not word:
                continue
            ipa: str | None = None
            sounds = obj.get("sounds") or []
            if sounds:
                ipa = (sounds[0].get("ipa") or "").strip() or None
            pos = _normalize_pos(obj.get("pos"))
            zh_tr = ""
            for t in obj.get("translations") or []:
                if t.get("code") == "zh":
                    zh_tr = (t.get("word") or "").strip()
                    break
            zh_tr = _convert_zh(zh_tr)
            forms = obj.get("forms") or []
            exchange: str | None = None
            if forms:
                exchange = ";".join(f.get("form", "") for f in forms if f.get("form"))[:500] or None
            yield word, ipa, zh_tr, pos, exchange


# ── UPSERT（缺項補，冪等） ─────────────────────────────


_UPSERT_SQL = """
insert into public.dict_cache (word, ipa, pos, translation, exchange)
values (%s, %s, %s::jsonb, %s, %s)
on conflict (word) do update set
    ipa         = coalesce(excluded.ipa, public.dict_cache.ipa),
    pos         = case
        when jsonb_array_length(public.dict_cache.pos) = 0
        then excluded.pos else public.dict_cache.pos
    end,
    translation = case
        when public.dict_cache.translation = ''
        then excluded.translation else public.dict_cache.translation
    end,
    exchange    = coalesce(excluded.exchange, public.dict_cache.exchange)
"""


def _encode(rows: list[tuple]) -> list[tuple]:
    """pos list 序列化成 jsonb 字串；psycopg 期待 list/tuple 全打包。"""
    return [(w, ipa, json.dumps(pos, ensure_ascii=False), tr, ex) for (w, ipa, tr, pos, ex) in rows]


async def _upsert_rows(rows: list[tuple]) -> int:
    if not rows:
        return 0
    async with connection() as conn, conn.cursor() as cur:
        await cur.executemany(_UPSERT_SQL, _encode(rows))
    return len(rows)


# ── driver ────────────────────────────────────────────


async def _load(source_iters: list[tuple[str, Iterator[tuple]]], batch: int) -> dict[str, int]:
    """依序跑各來源，每批 batch 列 flush。回傳 {'kaikki': n, 'ecdict': n}。"""
    counts: dict[str, int] = {}
    for label, gen in source_iters:
        n = 0
        buf: list[tuple] = []
        for row in gen:
            buf.append(row)
            if len(buf) >= batch:
                n += await _upsert_rows(buf)
                buf.clear()
        if buf:
            n += await _upsert_rows(buf)
        counts[label] = n
        logger.info("%s 完成：%d 列（累積）", label, n)
    return counts


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ecdict-csv", type=Path, required=True, help="ECDICT CSV 檔路徑")
    p.add_argument(
        "--kaikki-jsonl",
        type=Path,
        default=None,
        help="kaikki English JSONL 檔路徑（建議給，補 IPA）",
    )
    p.add_argument("--batch", type=int, default=5000, help="每批 UPSERT 列數（預設 5000）")
    a = p.parse_args()

    if not a.ecdict_csv.exists():
        sys.exit(f"找不到 ECDICT CSV：{a.ecdict_csv}")

    sources: list[tuple[str, Iterator[tuple]]] = [
        ("ecdict", _iter_ecdict(a.ecdict_csv)),
    ]
    if a.kaikki_jsonl is not None:
        if not a.kaikki_jsonl.exists():
            sys.exit(f"找不到 kaikki JSONL：{a.kaikki_jsonl}")
        # kaikki 排前面（補 IPA 為主），ecdict 後跑（補 curated zh + pos）
        sources.insert(0, ("kaikki", _iter_kaikki(a.kaikki_jsonl)))

    async def runner() -> dict[str, int]:
        try:
            return await _load(sources, a.batch)
        finally:
            await close_pool()

    counts = asyncio.run(runner())
    logger.info("全部完成：%s，audio_url 待 Layer 1 Piper backfill", counts)


if __name__ == "__main__":
    _amain()
