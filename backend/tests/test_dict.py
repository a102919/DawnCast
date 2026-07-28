"""dict backfill 相關邏輯的單元測試。

涵蓋：
  - LLM 回應解析（_parse_text）：code fence / plain JSON / 雜訊
  - pos 標準化（_normalize_pos）：混雜符號
  - Layer 2 diff：mock DB + queue send，驗 missing 的字才入 queue
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from engine.llm.translate import _parse_text
from scripts.seed_dict_cache import _normalize_pos
from tests._db_fakes import FakeConnection as _BaseFakeConnection
from tests._db_fakes import FakeCursor as _BaseFakeCursor
from tests._db_fakes import fake_connection

# ── _parse_text ───────────────────────────────────────


def test_parse_text_code_fence() -> None:
    raw = """```json
{"translation":"滑鼠","ipa":"/maʊs/","pos":["n"]}
```"""
    assert _parse_text(raw) == {
        "translation": "滑鼠",
        "ipa": "/maʊs/",
        "pos": ["n"],
    }


def test_parse_text_plain_json() -> None:
    assert _parse_text('{"translation":"網路","pos":"n"}') == {
        "translation": "網路",
        "pos": ["n"],
    }


def test_parse_text_missing_fields_returns_partial() -> None:
    out = _parse_text('{"translation":"磁碟"}')
    assert out == {"translation": "磁碟"}


def test_parse_text_garbage_returns_none() -> None:
    assert _parse_text("not json at all") is None
    assert _parse_text("") is None


def test_parse_text_brace_extraction_fallback() -> None:
    """LLM 在 JSON 前後夾廢話時，從字串中抽第一個 {...}。"""
    out = _parse_text('Sure! Here: {"translation":"yes"} thanks.')
    assert out == {"translation": "yes"}


# ── _normalize_pos ────────────────────────────────────


def test_normalize_pos_strips_noise() -> None:
    assert _normalize_pos("n./v.") == ["n", "v"]
    assert _normalize_pos("/n") == ["n"]
    assert _normalize_pos("n.&v.") == ["n", "v"]
    assert _normalize_pos("adj") == ["adj"]
    assert _normalize_pos("") == []


# ── backfill_dict ──────────────────────────────────────


async def test_backfill_dict_only_sends_missing() -> None:
    """dict_cache 已有 → 不入 queue；缺 → 入 queue。"""
    from engine.pipeline import post_process
    from shared.models import TargetVocab

    existing_in_db = {"alpha", "gamma"}  # alpha + gamma 已在 DB
    sent_words: list[str] = []

    class _FakeCursor(_BaseFakeCursor):
        async def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> None:
            return None

        async def fetchall(self) -> list[tuple[str]]:
            return [(w,) for w in existing_in_db]

    class _FakeConn(_BaseFakeConnection):
        def cursor(self, **_: object) -> _FakeCursor:
            return _FakeCursor()

    async def _send(queue: str, body: dict[str, object]) -> int:
        sent_words.append(body["word"])  # type: ignore[arg-type]
        return 1

    with (
        patch.object(post_process, "connection", fake_connection(_FakeConn)),
        patch.object(post_process, "send", _send),
    ):
        n = await post_process.backfill_dict(
            [
                TargetVocab(word="alpha", explanation="existing"),
                TargetVocab(word="beta", explanation="missing 1"),
                TargetVocab(word="gamma", explanation="existing"),
                TargetVocab(word="Beta", explanation="dedupe after casefold"),
            ]
        )

    assert n == 1, f"expected exactly 1 enqueued, got {n}"
    assert sent_words == ["beta"], f"expected only 'beta' enqueued, got {sent_words}"
