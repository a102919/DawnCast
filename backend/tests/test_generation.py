"""寫稿回應解析測試（parse_engine_result / _split_long_lines）。全程 mock，不打外部 API。

舊三引擎 adapter（api_key / claude_code / minimax）與其 factory 已退役刪除，
production 統一走 langgraph_pod/chat.py——引擎行為測試見 test_langgraph_pod.py。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.pipeline.langgraph_pod.prompt import (
    EngineResult,
    _split_long_lines,
    parse_engine_result,
)
from shared.errors import GenerationError
from shared.models import ScriptLine

# loop_engineering.json 當作 LLM 的標準輸出 ground truth
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "loop_engineering.json"


def _fixture_text() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


# ── parse / code fence ────────────────────────────────────────


def test_parse_plain_json() -> None:
    result = parse_engine_result(_fixture_text(), engine="api_key", model="m", usage={})
    assert isinstance(result, EngineResult)
    speakers = {line.speaker for line in result.script.script}
    assert speakers == {"Alex", "Sarah"}
    assert len(result.script.script) >= 8
    assert all(line.zh for line in result.script.script)  # 逐行 zh


def test_parse_strips_code_fence() -> None:
    fenced = f"```json\n{_fixture_text()}\n```"
    result = parse_engine_result(fenced, engine="api_key", model="m", usage={})
    assert len(result.script.script) >= 8


def test_parse_invalid_raises_generation_error() -> None:
    with pytest.raises(GenerationError):
        parse_engine_result("not json at all", engine="api_key", model="m", usage={})


def test_parse_too_short_raises_generation_error() -> None:
    # 雙人但只有 2 行 → 違反 ≥8 行契約
    bad = json.dumps(
        {
            "topic": "x",
            "extracted_facts": ["a"],
            "target_vocab": [{"word": "w", "explanation": "e"}],
            "script": [
                {"speaker": "Alex", "text": "hi", "zh": "嗨"},
                {"speaker": "Sarah", "text": "yo", "zh": "喲"},
            ],
        }
    )
    with pytest.raises(GenerationError):
        parse_engine_result(bad, engine="api_key", model="m", usage={})


# ── _split_long_lines：過長段落保底切割 ────────────────────────


def test_split_long_lines_keeps_short_line_unchanged() -> None:
    line = ScriptLine(speaker="Alex", text="Hi there. How are you?", zh="嗨。你好嗎？")
    out = _split_long_lines([line])
    assert out == [line]


def test_split_long_lines_splits_long_english_line() -> None:
    text = (
        "Okay but here's the wild part. Bears don't actually sleep the whole time. "
        "They're more like in a very deep TV-watching mode. They hear things. "
        "They move a little. But they don't eat. They don't drink. "
        "They don't even go to the bathroom for months."
    )
    zh = (
        "好，但重點來了。熊其實不是真的整段時間都在睡覺。"
        "牠們比較像是超級深度的看電視模式。會聽到東西。"
        "會動一點。但牠們不吃。不喝。"
        "牠們甚至好幾個月不上廁所。"
    )
    line = ScriptLine(speaker="Alex", text=text, zh=zh, pause_before=True)
    out = _split_long_lines([line], max_words=30)

    assert len(out) > 1
    assert all(chunk.speaker == "Alex" for chunk in out)
    assert all(len(chunk.text.split()) <= 30 for chunk in out)
    # chapter 邊界語意只留在第一組
    assert out[0].pause_before is True
    assert all(not chunk.pause_before for chunk in out[1:])
    # 內容沒有遺失（切開再接回去等於原文，忽略空白差異）
    assert "".join(c.text for c in out).replace(" ", "") == text.replace(" ", "")
    assert "".join(c.zh for c in out) == zh


def test_split_long_lines_zh_boundary_aligns_with_en() -> None:
    """回歸：真實生成案例。zh 句數（5）比 en（6）少一句時，舊的句數比例映射會把
    「但裡面？」推到下一組（對應 en 的 "But inside?" 卻留在前一組）→ 字幕中英錯位。
    新演算法按累積長度比例對齊，切點應落在 en 邊界同一語意處。"""
    text = (
        "Okay, picture your brain as a computer. "
        "During the day, you're running programs — work, traffic, texts you forgot to reply to. "
        "At night, the screen looks off. But inside? "
        "It's doing maintenance. Defragging the hard drive."
    )
    zh = (
        "好，想像你的大腦是一台電腦。白天你在跑程式——工作、開車、忘記回覆的訊息。"
        "晚上螢幕看起來關機了。但裡面？它在進行維護，把硬碟重組。"
    )
    line = ScriptLine(speaker="Alex", text=text, zh=zh)
    out = _split_long_lines([line], max_words=30)

    assert len(out) == 2
    assert out[0].text.endswith("But inside?")
    assert out[0].zh.endswith("但裡面？")  # 舊版這句會漏掉、跑到 out[1]
    assert out[1].zh == "它在進行維護，把硬碟重組。"
    assert "".join(c.zh for c in out) == zh  # 內容無遺失


def test_split_long_lines_leaves_single_unpunctuated_sentence() -> None:
    line = ScriptLine(speaker="Sarah", text=" ".join(["word"] * 50), zh="一句沒有標點的長句子")
    out = _split_long_lines([line], max_words=30)
    assert out == [line]
