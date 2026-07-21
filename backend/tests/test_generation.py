"""寫稿回應解析測試（parse_engine_result / _split_long_lines）。全程 mock，不打外部 API。

舊三引擎 adapter（api_key / claude_code / minimax）與其 factory 已退役刪除，
production 統一走 langgraph_pod/chat.py——引擎行為測試見 test_langgraph_pod.py。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.generation.base import EngineResult
from engine.generation.prompt import _split_long_lines, parse_engine_result
from shared.errors import GenerationError
from shared.models import ScriptLine

# loop_engineering.json 當作 LLM 的標準輸出 ground truth
_FIXTURE = Path(__file__).resolve().parents[2] / "scripts" / "loop_engineering.json"


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


def test_split_long_lines_leaves_single_unpunctuated_sentence() -> None:
    line = ScriptLine(speaker="Sarah", text=" ".join(["word"] * 50), zh="一句沒有標點的長句子")
    out = _split_long_lines([line], max_words=30)
    assert out == [line]
