"""英文詞形規則式 lemma 化（鏡像 frontend/src/lib/dict.ts 的規則）。

回傳候選清單，**衍生（rule 推導的 lemma）放前面、原 word 放最後**。
此排序給 dict_cache 用：
    SELECT ... WHERE word = any(candidates) ORDER BY array_position(candidates, word) DESC LIMIT 1
DESC 取最晚出現者 = 候選清單中**最像 lemma** 的（已剔除原 word），
這樣「點 trees 查到 tree 完整釋義」會優先命中，且不會被現有髒 key 蓋住。

局限：純 rule-based，對 irregular（children/goes/better）不完美。
但這些字 cache miss 時 LLM 仍會兜底；錯誤的 lemma key 自帶封印、不會被其他查詢命中。
"""

from __future__ import annotations


def lemmatize(word: str) -> list[str]:
    """鏡像 frontend/src/lib/dict.ts:lemmatize 的規則。

    回傳順序：**[rule 推導的衍生, ..., 原 word 最後]**，給
    `ORDER BY array_position(...) DESC LIMIT 1` 用。
    """
    w = word.lower()
    candidates: list[str] = [w]

    # -ing → base (running → run, writing → write)
    if w.endswith("ing"):
        candidates.append(w[:-3])
        candidates.append(w[:-3] + "e")
        if len(w) > 5:
            candidates.append(w[:-4])  # doubling (running → run)
    # -ed → base
    if w.endswith("ed"):
        candidates.append(w[:-2])
        candidates.append(w[:-1])
        if len(w) > 4:
            candidates.append(w[:-3])  # doubling
    # -s / -es
    if w.endswith("ies"):
        candidates.append(w[:-3] + "y")
    if w.endswith("es"):
        candidates.append(w[:-2])
    if w.endswith("s") and not w.endswith("ss"):
        candidates.append(w[:-1])
    # -er / -est → base
    if w.endswith("er"):
        candidates.append(w[:-2])
    if w.endswith("est"):
        candidates.append(w[:-3])
    # -ly
    if w.endswith("ly"):
        candidates.append(w[:-2])

    # 去重 + 排序：衍生（不是 w 的）放前面、原 word 放最後
    seen: set[str] = set()
    derived: list[str] = []
    for c in candidates:
        if not c or c == w or c in seen:
            continue
        seen.add(c)
        derived.append(c)
    derived.append(w)
    return derived
