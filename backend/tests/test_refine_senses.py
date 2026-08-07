"""義項精煉的解析防護（migration 0030 / engine.llm.translate.refine_batch）。

LLM 回來的東西必經 _normalize_refined，這裡守的是「爛輸出不能污染 dict_cache」。
"""

from __future__ import annotations

from engine.llm.translate import _normalize_refined, core_sense_ok, mnemonic_ok


def test_senses_缺失整筆作廢() -> None:
    # 沒有 senses 就沒有卡片可畫，其他欄位單獨存在沒有意義
    assert _normalize_refined({"core_sense": "持續的流動", "example_en": "He runs."}) is None
    assert _normalize_refined({"senses": []}) is None
    assert _normalize_refined({"senses": "跑"}) is None


def test_取前四筆並丟掉沒有中文的義項() -> None:
    out = _normalize_refined(
        {
            "senses": [
                {"pos": "vt.", "zh": "經營、管理"},
                {"pos": "vi.", "zh": ""},  # 空 zh 丟掉
                {"pos": "vi.", "zh": "跑、奔跑"},
                {"pos": "n.", "zh": "運轉時間"},
                {"pos": "a.", "zh": "熔化的"},
                {"pos": "x.", "zh": "第六個"},  # 超過 _MAX_SENSES
            ]
        }
    )
    assert out is not None
    # 前 4 筆進 window，其中空 zh 那筆被丟 → 剩 3 筆
    assert out["senses"] == [
        {"pos": "vt.", "zh": "經營、管理"},
        {"pos": "vi.", "zh": "跑、奔跑"},
        {"pos": "n.", "zh": "運轉時間"},
    ]


def test_單義字不留_core_sense() -> None:
    # core_sense 的價值是串起多個義項；只有一個義項時是純雜訊
    out = _normalize_refined(
        {"senses": [{"pos": "n.", "zh": "門檻"}], "core_sense": "跨過去的那條線"}
    )
    assert out is not None
    assert "core_sense" not in out


def test_多義字保留_core_sense() -> None:
    out = _normalize_refined(
        {
            "senses": [{"zh": "經營"}, {"zh": "跑"}],
            "core_sense": "持續的流動或運作",
        }
    )
    assert out is not None
    assert out["core_sense"] == "持續的流動或運作"


def test_字串型_null_當成沒填() -> None:
    # LLM 常把 null 寫成字串 "null"/"None"，直接寫進 DB 會讓前端渲染出 "null"
    out = _normalize_refined(
        {
            "senses": [{"pos": "null", "zh": "門檻"}],
            "example_en": "None",
            "example_zh": "",
            "mnemonic": "n/a",
        }
    )
    assert out is not None
    assert out["senses"] == [{"zh": "門檻"}]
    assert "example_en" not in out
    assert "mnemonic" not in out


def test_例句必須成對() -> None:
    # 只有英文沒中文的例句對台灣學習者沒用，兩個一起丟
    out = _normalize_refined(
        {"senses": [{"zh": "門檻"}], "example_en": "Sales reached the threshold."}
    )
    assert out is not None
    assert "example_en" not in out
    assert "example_zh" not in out


# --- 0031 加的確定性防線 --------------------------------------------------------
# prompt 是機率性的：0030 版光靠文字規則，58 筆 core_sense 超長、1387 筆諧音塌成
# 同一個句型。下面這幾條是「不管 prompt 怎麼漂，髒資料都進不了 DB」的那一層。


def test_core_sense_寫成詞源解釋會被退掉() -> None:
    # 實際案例：box →「四面封閉的空間，從盒子延伸為打拳的方場」
    assert not core_sense_ok("四面封閉的空間，從盒子延伸為打拳的方場")  # 又長又是解釋
    assert not core_sense_ok("封閉空間，亦指拳擊")  # 長度合格但仍是串接說明
    assert not core_sense_ok("熱度退去，引申為冷靜")
    assert core_sense_ok("持續的流動或運作")
    assert core_sense_ok("熱度退去的狀態")


def test_超長或違規的_core_sense_不寫進_payload() -> None:
    out = _normalize_refined(
        {
            "senses": [{"zh": "盒子"}, {"zh": "拳擊"}],
            "core_sense": "四面封閉的空間，從盒子延伸為打拳的方場",
        }
    )
    assert out is not None
    assert out["senses"] == [{"zh": "盒子"}, {"zh": "拳擊"}]
    assert "core_sense" not in out  # 寧可留白也不要一句解釋


def test_諧音門檻擋掉短字與模板句() -> None:
    assert not mnemonic_ok("the", "發音像『ㄉㄜ』，聯想到『的』")  # 短字 + 模板
    assert not mnemonic_ok("month", "「忙死」→ 過完一個月忙死了")  # 5 字母，不到門檻
    assert not mnemonic_ok("ambulance", "發音像『俺不能死』，聯想到救護車")  # 模板指紋
    assert not mnemonic_ok("ambulance", "俺" * 31)  # 超過 30 字
    # 詞根拆解可以帶英文，但英文必須真的是這個字的一段（否則模型在講別的字）
    assert not mnemonic_ok("company", "company 像 campaign 一起打仗")
    assert mnemonic_ok("transport", "trans(橫越)+port(港口) → 運過海港")
    assert mnemonic_ok("ambulance", "俺不能死 → 叫救護車")


def test_refine_不再產出諧音() -> None:
    # 諧音移到 mnemonic_batch 自己的 pass（溫度需求相反），refine 回來的一律忽略
    out = _normalize_refined(
        {"senses": [{"zh": "救護車"}], "mnemonic": "俺不能死 → 叫救護車"}
    )
    assert out is not None
    assert "mnemonic" not in out


def test_截斷的批次輸出救回完整物件() -> None:
    # 實跑事故：輸出撞到 max_tokens 停在半個陣列，25 個字整批作廢、連三次觸發中止
    from engine.llm.translate import _parse_batch_text

    truncated = (
        '[{"word":"legislator","senses":[{"pos":"n.","zh":"立法委員"}],'
        '"example_en":"The legislator proposed a bill."},'
        '{"word":"cheer","senses":[{"pos":"n.","zh":"歡呼"}]},'
        '{"word":"halt","senses":[{"pos":"v.","zh":"停'
    )
    items = _parse_batch_text(truncated)
    assert items is not None
    assert [i["word"] for i in items] == ["legislator", "cheer"]  # halt 留給下一輪


def test_引號裡的大括號不會誤判物件邊界() -> None:
    from engine.llm.translate import _parse_batch_text

    items = _parse_batch_text('[{"word":"brace","example_en":"Type \\"}\\" here."},{')
    assert items is not None
    assert [i["word"] for i in items] == ["brace"]
