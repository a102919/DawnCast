"""題目正規化：確定性正規化 + 輕量 LLM 收斂（MVP 先簡化）。

兩段：
1. deterministic_normalize：純函式，不依賴外部。NFKC + casefold + 去標點 + trim，
   讓「Quantum Computing」「quantum computing」「quantum   computing」落到同一等價類。
   這是分桶與快取 key 的基礎，必須 deterministic（同輸入永遠同輸出）。
2. llm_canonicalize：把 raw_topic 收斂成具體題目 + 判 topic_type。
   MVP 先不打 LLM（省成本、避免夜間批次受外部限流牽連），直接回 deterministic 結果，
   topic_type 預設 'evergreen'。LLM 介面留好，V1.1 接上只改這一個函式內部。
"""

from __future__ import annotations

import unicodedata

from engine.generation.base import GenerationEngine
from shared.models import TopicType

# 去標點：保留字母、數字、空白與 CJK；其餘視為分隔轉空白。
_KEEP_CATEGORIES = ("L", "N")  # Letter / Number（含 CJK，unicodedata 歸 Lo）


def deterministic_normalize(s: str) -> str:
    """確定性正規化：同義輸入 → 同一字串。純函式、無副作用。

    步驟：NFKC（全形半形統一）→ 標點轉空白 → casefold（比 lower 更徹底）
    → 合併連續空白 → trim。
    """
    nfkc = unicodedata.normalize("NFKC", s)
    chars: list[str] = []
    for ch in nfkc:
        if ch.isspace():
            chars.append(" ")
        elif unicodedata.category(ch)[0] in _KEEP_CATEGORIES:
            chars.append(ch)
        else:
            chars.append(" ")  # 標點 / 符號 → 空白（不是直接刪，避免黏字）
    collapsed = " ".join("".join(chars).split())
    return collapsed.casefold()


async def llm_canonicalize(
    raw_topic: str,
    engine: GenerationEngine,
) -> tuple[str, TopicType]:
    """把 raw_topic 收斂成 canonical 題目並判 topic_type。

    MVP：不打 LLM，直接回 deterministic_normalize + 'evergreen'。
    這讓夜間批次不被輕量收斂的外部呼叫拖慢 / 受限流牽連；題目品質由寫稿主呼叫負責。

    V1.1 啟用 LLM 時，只改這個函式內部：用 engine 發一個輕量分類 prompt，
    回 (canonical_topic, topic_type)；介面（含 engine 參數）已留好，呼叫端零改動。
    """
    _ = engine  # V1.1 才用；先保留參數避免之後改簽名
    canonical = deterministic_normalize(raw_topic)
    topic_type: TopicType = "evergreen"
    return canonical, topic_type
