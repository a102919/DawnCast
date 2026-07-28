"""腳本契約規則的共用純函式。

寫稿 pipeline 有兩層契約檢查，各自獨立實作過同一套規則，容易漂移：
  * Level 1（段落層級，engine/pipeline/langgraph_pod/nodes.py）：單段生成完立刻
    預檢，沒過就重打這一段。
  * Level 2（合併後，shared/models/engine.py 的 ScriptJSON model_validator）：
    全集合併後的權威契約，沒過整輪重打。

兩層都要判斷「target_vocab 是否真的出現在文字裡（含詞形變化）」與「相鄰兩行是否
完全重複」，抽成這裡的純函式，兩處各自 import 呼叫，不再各自維護一份 regex/
lemma pool 建構邏輯。
"""

from __future__ import annotations

import re

from shared.lemmatize import lemmatize

_WORD_RE = re.compile(r"[A-Za-z']+")


def build_lemma_pool(text: str) -> set[str]:
    """把一段 text 斷詞、lemmatize，回傳所有候選 lemma 的集合。"""
    pool: set[str] = set()
    for token in _WORD_RE.findall(text.casefold()):
        pool.update(lemmatize(token))
    return pool


def missing_vocab_words(text: str, vocab_words: list[str]) -> list[str]:
    """回傳「在 text 裡沒出現（含詞形變化都沒有）」的 vocab_words。空 list = 全部命中。

    片語（含空白/連字號，如 "cancel out"）拆成單字後逐一比對同一個 lemma_pool，
    不用整段字串比對，這樣才能吃到片語動詞的詞形變化，也不會因為對話把片語拆開講
    （動詞跟受詞插在片語中間）而誤判成沒出現。
    """
    pool = build_lemma_pool(text)
    missing: list[str] = []
    for w in vocab_words:
        w_lower = w.casefold()
        if " " in w_lower or "-" in w_lower:
            parts = [p for p in re.split(r"[\s-]+", w_lower) if p]
            if not all(p in pool for p in parts):
                missing.append(w)
        elif w_lower not in pool:
            missing.append(w)
    return missing


def first_duplicate_adjacent_index(values: list[str]) -> int | None:
    """回傳第一個跟前一個值完全相同的 index；None = 沒有重複。

    LLM 偶爾會把中英句界對不上，內容往下一行偏移，累積到最後兩個連續行完全重複——
    這是對齊漂移的訊號，攔下來重寫比放行更划算。
    """
    for i in range(1, len(values)):
        if values[i] == values[i - 1]:
            return i
    return None
