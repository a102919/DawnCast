"""/dict/lookup lemma 化 SQL 行為的整合測試。

驗證 lookup_dict 的 lemma 候選 SQL（`WHERE word = ANY(candidates)
ORDER BY array_position(candidates, word) DESC NULLS LAST LIMIT 1`）會：
  - cache 同時有 lemma 與原 word → 優先回 lemma（最像 lemma 的命中）
  - cache 只有 lemma → 回 lemma
  - cache 只有原 word → 回原 word
  - cache 沒命中 → 走 LLM fallback，寫回 dict_cache 後讀回

SQL 是 PostgreSQL 標準函式，不在這層重做；FakeCursor 只負責模擬
"候選清單中位置最晚的命中優先"這條語意。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.routers import dict as dict_router
from shared.db import pool as db_pool
from tests._auth import auth_header

USER_ID = "11111111-1111-1111-1111-111111111111"
LOOKUP_PATH = "/dict/lookup"

# ── Fake DB（multi-row cache，模擬 array_position 排序）──────────────


# 每個 word 一筆 row；測試用 fixture 在 setup 餵資料。
_CACHE_ROWS: dict[str, dict[str, Any]] = {}


def _seed_cache(*rows: dict[str, Any]) -> None:
    """給定 dict 序列寫入 fake cache；後面遇到重複 word 後寫的覆蓋前寫的。"""
    _CACHE_ROWS.clear()
    for r in rows:
        _CACHE_ROWS[r["word"]] = r


class _FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._rows = []
        normalized = " ".join(sql.split())

        # Lemma 候選 SELECT：模擬 array_position(candidates, word) DESC 排序。
        # candidates = 原 word 首位、衍生依序往後（DESC 取最晚 → 最像 lemma 的）。
        if "from public.dict_cache" in normalized and "where word = any(%s::text[])" in normalized:
            candidates: list[str] = list(params[0])
            hits = [dict(_CACHE_ROWS[w]) for w in candidates if w in _CACHE_ROWS]
            # 1-based array_position：DESC 取最高位置。
            hits.sort(key=lambda r: candidates.index(r["word"]), reverse=True)
            self._rows = hits[:1]
            return

        # LLM fallback 寫回後讀回：給原 word。
        if "from public.dict_cache where word = %s" in normalized:
            word = params[0]
            if isinstance(word, str) and word in _CACHE_ROWS:
                self._rows = [dict(_CACHE_ROWS[word])]
            return

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeConnection:
    def cursor(self, **_: object) -> _FakeCursor:
        return _FakeCursor()

    async def commit(self) -> None:
        return None


@asynccontextmanager
async def fake_connection() -> AsyncIterator[_FakeConnection]:
    yield _FakeConnection()


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    _CACHE_ROWS.clear()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dict_router, "connection", fake_connection)
    monkeypatch.setattr(db_pool, "connection", fake_connection)


@pytest.fixture
def make_client() -> TestClient:
    from app.main import create_app

    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ── (1) Lemma 命中壓過原 word ───────────────────────────────────────


def test_lemma_wins_over_original_word(make_client: TestClient) -> None:
    """cache 同時有 tree 與 trees → 查 trees 必須回 tree（lemma 優先）。"""
    _seed_cache(
        {
            "word": "tree",
            "ipa": "/triː/",
            "pos": ["n"],
            "translation": "樹",
            "exchange": None,
            "audio_url": None,
            "example_en": "an old tree",
            "example_zh": "一棵老樹",
        },
        {
            "word": "trees",
            "ipa": None,
            "pos": [],
            "translation": "trees（薄資料）",
            "exchange": None,
            "audio_url": None,
            "example_en": None,
            "example_zh": None,
        },
    )
    res = make_client.get(f"{LOOKUP_PATH}?w=trees", headers=auth_header(USER_ID))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["word"] == "tree"
    assert body["data"]["translation"] == "樹"


# ── (2) 只有 lemma：仍命中 ─────────────────────────────────────────


def test_lemma_only_hit(make_client: TestClient) -> None:
    _seed_cache(
        {
            "word": "tree",
            "ipa": "/triː/",
            "pos": ["n"],
            "translation": "樹",
            "exchange": None,
            "audio_url": None,
            "example_en": None,
            "example_zh": None,
        },
    )
    res = make_client.get(f"{LOOKUP_PATH}?w=trees", headers=auth_header(USER_ID))
    assert res.status_code == 200
    body = res.json()
    assert body["data"]["word"] == "tree"


# ── (3) 只有原 word：fallback 回原 word（行為相容於改動前）────────


def test_only_original_word_hit(make_client: TestClient) -> None:
    _seed_cache(
        {
            "word": "trees",
            "ipa": None,
            "pos": [],
            "translation": "樹（複數）",
            "exchange": None,
            "audio_url": None,
            "example_en": None,
            "example_zh": None,
        },
    )
    res = make_client.get(f"{LOOKUP_PATH}?w=trees", headers=auth_header(USER_ID))
    assert res.status_code == 200
    body = res.json()
    assert body["data"]["word"] == "trees"


# ── (4) 完全 miss：LLM fallback 寫回後讀回 ─────────────────────────


def test_cache_miss_falls_through_to_llm(make_client: TestClient) -> None:
    """cache 完全沒有候選命中 → 走 translate_word；寫回後讀回原 word 那筆。"""
    llm_payload = {
        "translation": "測試翻譯",
        "ipa": "/test/",
        "pos": ["n"],
        "example_en": "a test",
        "example_zh": "一個測試",
    }
    captured: dict[str, str] = {}

    async def fake_translate(word: str) -> dict[str, Any] | None:
        captured["word"] = word
        # 模擬 dict.py 在 LLM fallback 寫回 dict_cache 後讀回的那筆
        _CACHE_ROWS[word] = {
            "word": word,
            "ipa": llm_payload["ipa"],
            "pos": llm_payload["pos"],
            "translation": llm_payload["translation"],
            "exchange": None,
            "audio_url": None,
            "example_en": llm_payload["example_en"],
            "example_zh": llm_payload["example_zh"],
        }
        return llm_payload

    # patch 綁在 dict_router 上的 translate_word（dict.py 用 from-import 綁死）
    with patch.object(dict_router, "translate_word", side_effect=fake_translate):
        res = make_client.get(f"{LOOKUP_PATH}?w=zoo", headers=auth_header(USER_ID))

    assert captured.get("word") == "zoo", "LLM fallback 沒被觸發"
    assert res.status_code == 200
    body = res.json()
    assert body["data"]["word"] == "zoo"
    assert body["data"]["translation"] == "測試翻譯"
