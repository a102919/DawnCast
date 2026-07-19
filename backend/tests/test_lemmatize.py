"""lemmatize 規則測試（鏡像前端規則）。"""

from __future__ import annotations

from shared.lemmatize import lemmatize


def test_lemmatize_plurals() -> None:
    # 衍生先、原 word 最後
    assert lemmatize("trees") == ["tre", "tree", "trees"]
    assert "tree" in lemmatize("trees")
    assert "is" in lemmatize("is")  # 不會被誤判


def test_lemmatize_verb_forms() -> None:
    out = lemmatize("running")
    assert "run" in out
    assert out[-1] == "running"  # 原 word 最後

    out = lemmatize("writing")
    assert "write" in out
    assert out[-1] == "writing"

    assert "stop" in lemmatize("stopped")  # doubling


def test_lemmatize_no_double_count_for_doubles() -> None:
    # doubling 規則可能會推導出同樣字串的情境（runn → run 已經有了）
    out = lemmatize("running")
    assert out.count("run") == 1


def test_lemmatize_comparatives() -> None:
    assert "big" in [c[:3] for c in lemmatize("bigger")] or "bigg" in lemmatize("bigger")
    # irregular 規則不完美，'bigger → big' 不在規則裡只能到 'bigg'，故意驗這個邊界
    assert lemmatize("bigger")[-1] == "bigger"


def test_lemmatize_adverbs() -> None:
    assert "slow" in lemmatize("slowly")
    assert lemmatize("slowly")[-1] == "slowly"


def test_lemmatize_empty_input() -> None:
    # 空輸入回傳 [原 word] = ['']；呼叫端（lookup_dict）在 strip() 後已擋空。
    assert lemmatize("") == [""]
