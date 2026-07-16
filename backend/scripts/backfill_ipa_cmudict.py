"""補 dict_cache.ipa（CMUdict ARPABET → IPA），只補目前缺 IPA 的既有字。

只 UPDATE 既有列（ipa 為 NULL 或空字串），不 INSERT 新字 —
沒翻譯的字對查字典用途不大，交給 seed_dict_cache 的來源負責新增字。

來源（手動下載，script 不替你抓）：
  github.com/cmusphinx/cmudict → cmudict.dict（BSD license，公開領域等級授權）

執行：
  uv run python -m scripts.backfill_ipa_cmudict --cmudict-dict /tmp/cmudict.dict
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from collections.abc import Iterator
from pathlib import Path

from shared.db.pool import close_pool, connection

logger = logging.getLogger(__name__)

# ARPABET（CMUdict 音素集）→ IPA，重音數字另外處理（AH/ER 依重音變體）。
_ARPABET_IPA = {
    "AA": "ɑ",
    "AE": "æ",
    "AO": "ɔ",
    "AW": "aʊ",
    "AY": "aɪ",
    "EH": "ɛ",
    "EY": "eɪ",
    "IH": "ɪ",
    "IY": "i",
    "OW": "oʊ",
    "OY": "ɔɪ",
    "UH": "ʊ",
    "UW": "u",
    "B": "b",
    "CH": "tʃ",
    "D": "d",
    "DH": "ð",
    "F": "f",
    "G": "ɡ",
    "HH": "h",
    "JH": "dʒ",
    "K": "k",
    "L": "l",
    "M": "m",
    "N": "n",
    "NG": "ŋ",
    "P": "p",
    "R": "ɹ",
    "S": "s",
    "SH": "ʃ",
    "T": "t",
    "TH": "θ",
    "V": "v",
    "W": "w",
    "Y": "j",
    "Z": "z",
    "ZH": "ʒ",
}
_STRESS_MARK = {"1": "ˈ", "2": "ˌ"}
_WORD_RE = re.compile(r"^[a-z]+$")


def _phoneme_to_ipa(ph: str) -> str:
    m = re.match(r"^([A-Z]+)([012])?$", ph)
    if not m:
        return ""
    base, stress = m.group(1), m.group(2) or "0"
    if base == "AH":
        symbol = "ə" if stress == "0" else "ʌ"
    elif base == "ER":
        symbol = "ɚ" if stress == "0" else "ɝ"
    else:
        symbol = _ARPABET_IPA.get(base, "")
    return _STRESS_MARK.get(stress, "") + symbol


def _iter_cmudict(path: Path) -> Iterator[tuple[str, str]]:
    """cmudict.dict → (word, ipa)。同字重複發音只取第一個（含 '(2)' 變體行整行跳過）。"""
    seen: set[str] = set()
    with path.open(encoding="latin-1") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";;;"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            raw_word, phonemes = parts[0], parts[1:]
            if "(" in raw_word:  # 變體發音（第二種以上），跳過只留主要發音
                continue
            word = raw_word.casefold()
            if word in seen or not _WORD_RE.match(word):
                continue
            seen.add(word)
            ipa = "".join(_phoneme_to_ipa(p) for p in phonemes)
            if ipa:
                yield word, f"/{ipa}/"


_UPDATE_SQL = """
update public.dict_cache set ipa = %s
where word = %s and (ipa is null or ipa = '')
"""


async def _apply(rows: list[tuple[str, str]], batch: int) -> int:
    n = 0
    async with connection() as conn, conn.cursor() as cur:
        for i in range(0, len(rows), batch):
            chunk = rows[i : i + batch]
            await cur.executemany(_UPDATE_SQL, [(ipa, word) for word, ipa in chunk])
            n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
            logger.info("已處理 %d/%d 字", min(i + batch, len(rows)), len(rows))
    return n


def _amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--cmudict-dict", type=Path, required=True, help="cmudict.dict 檔路徑")
    p.add_argument("--batch", type=int, default=5000, help="每批 UPDATE 列數（預設 5000）")
    a = p.parse_args()

    if not a.cmudict_dict.exists():
        sys.exit(f"找不到 cmudict.dict：{a.cmudict_dict}")

    rows = list(_iter_cmudict(a.cmudict_dict))
    logger.info("cmudict 解析完成：%d 字（去重＋只留主要發音）", len(rows))

    async def runner() -> int:
        try:
            return await _apply(rows, a.batch)
        finally:
            await close_pool()

    updated = asyncio.run(runner())
    logger.info("完成：實際補上 ipa 的列數（近似值，UPDATE rowcount 累加）=%d", updated)


if __name__ == "__main__":
    _amain()
