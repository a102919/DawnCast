"""生成引擎 adapter 測試。全程 mock，不打外部 API。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from engine.generation.api_key import ApiKeyEngine
from engine.generation.base import EngineResult, GenerationRequest
from engine.generation.claude_code import ClaudeCodeEngine
from engine.generation.factory import make_engine
from engine.generation.minimax import MinimaxEngine
from engine.generation.prompt import _split_long_lines, build_messages, parse_engine_result
from shared.config import Settings
from shared.errors import ConfigError, GenerationError, RateLimitError
from shared.models import ScriptLine

# loop_engineering.json 當作 LLM 的標準輸出 ground truth
_FIXTURE = Path(__file__).resolve().parents[2] / "scripts" / "loop_engineering.json"


def _fixture_text() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "api_key": "test-key",
        "api_base_url": "https://example.test/anthropic",
        "api_model": "Test-Model",
        "minimax_auth_token": "test-token",
        "minimax_anthropic_base_url": "https://example.test/anthropic",
        "minimax_model": "Test-MiniMax",
        "http_max_retries": 2,
        "generation_max_attempts": 3,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _req() -> GenerationRequest:
    return GenerationRequest(
        canonical_topic="Loop Engineering",
        big_topic="AI Coding",
        topic_type="evergreen",
        angle="定義",
    )


def _anthropic_response(text: str) -> dict[str, object]:
    """包成 Anthropic Messages 回應格式。"""
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 100, "output_tokens": 800},
    }


def _make_engine_with_transport(handler: httpx.MockTransport) -> ApiKeyEngine:
    engine = ApiKeyEngine(_settings())
    # 保留引擎原本設好的 headers（Authorization 等），只換掉底層 transport 攔截網路
    engine._client = httpx.AsyncClient(  # noqa: SLF001 測試替換 transport
        base_url="https://example.test/anthropic",
        headers=engine._client.headers,  # noqa: SLF001
        transport=handler,
    )
    return engine


# ── prompt 組裝 ────────────────────────────────────────────────


def test_build_messages_shape() -> None:
    messages = build_messages(_req())
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user"]
    system = messages[0]["content"]
    assert "Alex" in system and "Sarah" in system
    assert "B1" in system  # CEFR
    assert "定義" in system  # 角度
    assert "code fence" in system.lower()  # 要求不要 fence
    assert "Loop Engineering" in messages[1]["content"]


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


# ── ApiKeyEngine.write_script 走通 ─────────────────────────────


@pytest.mark.asyncio
async def test_write_script_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/anthropic/v1/messages"
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, json=_anthropic_response(_fixture_text()))

    engine = _make_engine_with_transport(httpx.MockTransport(handler))
    result = await engine.write_script(_req())
    await engine.aclose()

    assert result.engine == "api_key"
    speakers = {line.speaker for line in result.script.script}
    assert speakers == {"Alex", "Sarah"}
    assert len(result.script.script) >= 8
    assert all(line.zh for line in result.script.script)
    assert result.raw_usage["output_tokens"] == 800


@pytest.mark.asyncio
async def test_write_script_fenced_response() -> None:
    fenced = f"```json\n{_fixture_text()}\n```"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_anthropic_response(fenced))

    engine = _make_engine_with_transport(httpx.MockTransport(handler))
    result = await engine.write_script(_req())
    await engine.aclose()
    assert len(result.script.script) >= 8


@pytest.mark.asyncio
async def test_write_script_429_raises_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    engine = _make_engine_with_transport(httpx.MockTransport(handler))
    with pytest.raises(RateLimitError):
        await engine.write_script(_req())
    await engine.aclose()


@pytest.mark.asyncio
async def test_write_script_retries_then_gives_up() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_anthropic_response("garbage not json"))

    engine = _make_engine_with_transport(httpx.MockTransport(handler))
    with pytest.raises(GenerationError):
        await engine.write_script(_req())
    await engine.aclose()
    # 語意層硬上限 = generation_max_attempts = 3
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_write_script_500_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json=_anthropic_response(_fixture_text()))

    engine = _make_engine_with_transport(httpx.MockTransport(handler))
    result = await engine.write_script(_req())
    await engine.aclose()
    assert calls["n"] == 2  # 第一次 500 重試後成功
    assert len(result.script.script) >= 8


# ── health ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_reflects_token() -> None:
    assert await ApiKeyEngine(_settings(api_key="k")).health() is True
    assert await ApiKeyEngine(_settings(api_key="")).health() is False


# ── factory env 切換 ──────────────────────────────────────────


def test_factory_selects_minimax() -> None:
    engine = make_engine(_settings(generation_engine="minimax"))
    assert isinstance(engine, MinimaxEngine)
    assert engine.name == "minimax"


def test_factory_selects_api_key() -> None:
    engine = make_engine(_settings(generation_engine="api_key"))
    assert isinstance(engine, ApiKeyEngine)
    assert engine.name == "api_key"


def test_factory_selects_claude_code() -> None:
    engine = make_engine(_settings(generation_engine="claude_code"))
    assert isinstance(engine, ClaudeCodeEngine)


def test_factory_unknown_raises_config_error() -> None:
    bad = _settings()
    object.__setattr__(bad, "generation_engine", "nope")
    with pytest.raises(ConfigError):
        make_engine(bad)
