"""英文詞形規則式 lemma 化（鏡像 frontend/src/lib/dict.ts 的規則）。

回傳候選清單，**原 word 放最前、衍生（rule 推導的 lemma）依序往後**。
此排序給 dict_cache 用：
    SELECT ... WHERE word = any(candidates) ORDER BY array_position(candidates, word) DESC LIMIT 1
DESC 取**最晚出現者** = 候選清單中位置最靠後的命中；衍生在後段，所以 cache 有
lemma 條目時會壓過原 word 命中（解決「點 trees 查到 tree 完整釋義」）；
原 word 永遠在首位，cache 只有原 word 時仍會被命中。

局限：純 rule-based，對 irregular（children/goes/better）不完美。
但這些字 cache miss 時 LLM 仍會兜底；錯誤的 lemma key 自帶封印、不會被其他查詢命中。
"""

from __future__ import annotations


def lemmatize(word: str) -> list[str]:
    """鏡像 frontend/src/lib/dict.ts:lemmatize 的規則。

    回傳順序：**[原 word, ..., 衍生依序往後]**，給
    `ORDER BY array_position(...) DESC LIMIT 1` 用（DESC 讓位置最晚
    也就是最像 lemma 的命中壓過原 word）。
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

    # 去重：原 word 在首位，衍生依出現順序往後
    seen: set[str] = {w}
    out: list[str] = [w]
    for c in candidates[1:]:
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out
