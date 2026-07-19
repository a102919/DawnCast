"""lemmatize 規則測試（鏡像前端規則）。"""

from __future__ import annotations

from shared.lemmatize import lemmatize


def test_lemmatize_plurals() -> None:
    # 原 word 在首位、衍生依序往後（給 SQL ORDER BY array_position DESC 用）
    assert lemmatize("trees") == ["trees", "tre", "tree"]
    assert lemmatize("trees")[0] == "trees"  # 原 word 一定在首位
    assert lemmatize("trees")[-1] == "tree"  # 最像 lemma 的放最後（DESC 會揀它）
    assert "is" in lemmatize("is")  # 不會被誤判


def test_lemmatize_verb_forms() -> None:
    out = lemmatize("running")
    assert out[0] == "running"  # 原 word 首位
    assert "run" in out
    assert out[-1] == "run"  # doubling 規則 w[:-4] 命中正確

    out = lemmatize("writing")
    assert out[0] == "writing"
    assert "write" in out
    # doubling 規則 w[:-4] 對 "writing" 會錯推 "wri" — 已知 rule-based 限制，
    # 故意只驗「write 是候選之一」，不驗位置。
    assert "wri" in out  # 文件化此限制

    assert "stop" in lemmatize("stopped")  # doubling


def test_lemmatize_no_double_count_for_doubles() -> None:
    # doubling 規則可能會推導出同樣字串的情境（runn → run 已經有了）
    out = lemmatize("running")
    assert out.count("run") == 1


def test_lemmatize_comparatives() -> None:
    assert "bigg" in lemmatize("bigger")
    # irregular 規則不完美，'bigger → big' 不在規則裡只能到 'bigg'，故意驗這個邊界
    assert lemmatize("bigger")[0] == "bigger"  # 原 word 首位


def test_lemmatize_adverbs() -> None:
    assert "slow" in lemmatize("slowly")
    assert lemmatize("slowly")[0] == "slowly"


def test_lemmatize_empty_input() -> None:
    # 空輸入回傳 [原 word] = ['']；呼叫端（lookup_dict）在 strip() 後已擋空。
    assert lemmatize("") == [""]
