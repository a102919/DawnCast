"""T3 PronounceButton 音檔 backfill 測試。

驗證：
  - 查無音檔時觸發 TTS 並回寫 audio_url（cache hit + LLM fallback 兩路徑）
  - 已有音檔不重複產生
  - TTS 失敗 / 非單字降級：200 + audioUrl null，不回 500
  - DB 回寫失敗 best-effort：仍回帶 URL 的 entry
  - 授權沿用既有 Depends(get_current_user)（無 token → 401）

全程 monkeypatch synthesize_word_audio 與 DB connection，不打真 Piper / R2。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import dict as dict_router
from engine.media import dict_audio
from shared.db import pool as db_pool

USER_ID = "11111111-1111-1111-1111-111111111111"

# ── Fake DB ──────────────────────────────────────────────────────────

# 全域可變 state：每個測試由 _reset_state fixture 清空。
_CACHE_ROW: dict[str, Any] | None = None
_EXECUTED_SQL: list[str] = []
_FORCE_UPDATE_EXC: bool = False


def _set_cache(row: dict[str, Any] | None) -> None:
    global _CACHE_ROW
    _CACHE_ROW = row


class _FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())
        _EXECUTED_SQL.append(s)
        self._rows = []

        # SELECT by word（cache 命中查詢 / LLM fallback 後讀回）
        if "from public.dict_cache where word = %s" in s:
            word = params[0]
            if _CACHE_ROW is not None and _CACHE_ROW.get("word") == word:
                self._rows = [dict(_CACHE_ROW)]
            else:
                self._rows = []
            return

        # LLM fallback 的 INSERT
        if "insert into public.dict_cache" in s:
            _set_cache(
                {
                    "word": params[0],
                    "ipa": params[1],
                    "pos": json.loads(params[2]) if params[2] else [],
                    "translation": params[3],
                    "exchange": None,
                    "audio_url": None,
                    "example_en": params[4],
                    "example_zh": params[5],
                }
            )
            self._rows = []
            return

        # 寫回 audio_url 的 UPDATE（可能被注入失敗）
        if "update public.dict_cache set audio_url" in s:
            if _FORCE_UPDATE_EXC:
                raise RuntimeError("forced UPDATE failure")
            cur_row = _CACHE_ROW
            if cur_row is not None and cur_row.get("word") == params[1]:
                _set_cache({**cur_row, "audio_url": params[0]})
            self._rows = []
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
def _reset_state() -> None:
    global _FORCE_UPDATE_EXC
    _set_cache(None)
    _FORCE_UPDATE_EXC = False
    _EXECUTED_SQL.clear()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dict_router, "connection", fake_connection)
    monkeypatch.setattr(db_pool, "connection", fake_connection)


@pytest.fixture(autouse=True)
def patch_translate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_translate(word: str) -> dict[str, Any] | None:
        return {
            "translation": "測試翻譯",
            "ipa": "/test/",
            "pos": ["n"],
            "example_en": "a test",
            "example_zh": "測試一句",
        }

    monkeypatch.setattr(dict_router, "translate_word", _fake_translate)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _auth(user_id: str) -> dict[str, str]:
    from tests._auth import auth_header

    return auth_header(user_id)


def _async_fn(sync_fn: Any) -> Any:
    """把同步函式包成 async（給 mock router 用）。"""

    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        return sync_fn(*args, **kwargs)

    return _wrapper


def _async_return(called_with: list[str], value: str) -> Any:
    """monkeypatch 用的 async helper：記錄呼叫參數並回固定 URL。"""

    async def _fn(word: str) -> str:
        called_with.append(word)
        return value

    return _fn


# ── (1) cache hit, audio_url=null → 觸發 TTS 並回寫 ────────────────


def test_lookup_cache_hit_null_audio_triggers_tts_and_writes_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cache(
        {
            "word": "alpha",
            "ipa": "/ˈælfə/",
            "pos": ["n"],
            "translation": "α",
            "exchange": None,
            "audio_url": None,
            "example_en": "an alpha",
            "example_zh": "一個 α",
        }
    )

    called_with: list[str] = []
    monkeypatch.setattr(
        dict_router,
        "synthesize_word_audio",
        _async_return(called_with, "https://local/media/dict/alpha.wav"),
    )

    res = client.get("/dict/lookup?w=alpha", headers=_auth(USER_ID))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["audioUrl"] == "https://local/media/dict/alpha.wav"
    assert called_with == ["alpha"]
    assert any("update public.dict_cache set audio_url" in s for s in _EXECUTED_SQL)


# ── (2) cache hit 已有 audio_url → 不重複觸發 TTS ──────────────────


def test_lookup_cache_hit_with_audio_skips_tts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cache(
        {
            "word": "beta",
            "ipa": "/ˈbiːtə/",
            "pos": ["n"],
            "translation": "β",
            "exchange": None,
            "audio_url": "https://local/media/dict/beta.wav",
            "example_en": "a beta",
            "example_zh": "一個 β",
        }
    )

    call_count = 0

    def _must_not_call(w: str) -> str | None:
        nonlocal call_count
        call_count += 1
        return "https://should-not-appear/x.wav"

    monkeypatch.setattr(dict_router, "synthesize_word_audio", _async_fn(_must_not_call))

    res = client.get("/dict/lookup?w=beta", headers=_auth(USER_ID))
    assert res.status_code == 200
    assert res.json()["data"]["audioUrl"] == "https://local/media/dict/beta.wav"
    assert call_count == 0
    assert not any("update public.dict_cache set audio_url" in s for s in _EXECUTED_SQL)


# ── (3) LLM fallback 路徑也會觸發 TTS ──────────────────────────────


def test_lookup_llm_fallback_generates_audio(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cache(None)  # cache miss → LLM fallback

    monkeypatch.setattr(
        dict_router,
        "synthesize_word_audio",
        _async_fn(lambda w: f"https://local/media/dict/{w}.wav"),
    )

    res = client.get("/dict/lookup?w=gamma", headers=_auth(USER_ID))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["audioUrl"] == "https://local/media/dict/gamma.wav"
    assert any("insert into public.dict_cache" in s for s in _EXECUTED_SQL)
    assert any("update public.dict_cache set audio_url" in s for s in _EXECUTED_SQL)


# ── (4) TTS 失敗 → 200 + audioUrl null + 不回 500 ──────────────────


def test_lookup_tts_failure_degrades_no_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cache(
        {
            "word": "delta",
            "ipa": None,
            "pos": ["n"],
            "translation": "δ",
            "exchange": None,
            "audio_url": None,
            "example_en": None,
            "example_zh": None,
        }
    )

    def _boom(w: str) -> str | None:
        raise RuntimeError("piper 沒裝")

    monkeypatch.setattr(dict_router, "synthesize_word_audio", _async_fn(_boom))

    res = client.get("/dict/lookup?w=delta", headers=_auth(USER_ID))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert res.json()["data"]["audioUrl"] is None
    assert not any("update public.dict_cache set audio_url" in s for s in _EXECUTED_SQL)


# ── (5) synthesize 回 None（守門命中）────────────────────────────


def test_lookup_synth_none_returns_null_audio(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_cache(
        {
            "word": "epsilon",
            "ipa": None,
            "pos": ["n"],
            "translation": "ε",
            "exchange": None,
            "audio_url": None,
            "example_en": None,
            "example_zh": None,
        }
    )

    monkeypatch.setattr(dict_router, "synthesize_word_audio", _async_fn(lambda w: None))

    res = client.get("/dict/lookup?w=epsilon", headers=_auth(USER_ID))
    assert res.status_code == 200
    assert res.json()["data"]["audioUrl"] is None
    assert not any("update public.dict_cache set audio_url" in s for s in _EXECUTED_SQL)


# ── (6) DB UPDATE 失敗仍回帶 URL 的 entry（best-effort）────────────


def test_lookup_writeback_failure_still_returns_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    global _FORCE_UPDATE_EXC
    _set_cache(
        {
            "word": "zeta",
            "ipa": None,
            "pos": ["n"],
            "translation": "ζ",
            "exchange": None,
            "audio_url": None,
            "example_en": None,
            "example_zh": None,
        }
    )
    _FORCE_UPDATE_EXC = True

    monkeypatch.setattr(
        dict_router,
        "synthesize_word_audio",
        _async_fn(lambda w: "https://local/media/dict/zeta.wav"),
    )

    res = client.get("/dict/lookup?w=zeta", headers=_auth(USER_ID))
    assert res.status_code == 200
    assert res.json()["data"]["audioUrl"] == "https://local/media/dict/zeta.wav"


# ── (7) 非單字（含空白）→ 不觸發 TTS（守門在 helper 內）────────────


def test_lookup_phrase_skips_tts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """非單字查詢：audioUrl 必須是 null（PronounceButton !audioUrl 不顯示）。

    helper 內 ^[a-z]+$ 守門會回 None；router 端呼叫後降級。
    真正的 TTS 不會被觸發（mock 收到呼叫但內部不跑 piper）。
    """
    _set_cache(
        {
            "word": "hello world",
            "ipa": None,
            "pos": ["phrase"],
            "translation": "你好世界",
            "exchange": None,
            "audio_url": None,
            "example_en": None,
            "example_zh": None,
        }
    )

    # mock 即使被呼叫也回 None（模擬 helper 內 ^[a-z]+$ 守門命中）
    monkeypatch.setattr(dict_router, "synthesize_word_audio", _async_fn(lambda w: None))

    res = client.get("/dict/lookup?w=hello%20world", headers=_auth(USER_ID))
    assert res.status_code == 200
    assert res.json()["data"]["audioUrl"] is None
    # 也驗證沒有 UPDATE 回寫（audioUrl 沒拿到新 URL）
    assert not any("update public.dict_cache set audio_url" in sql for sql in _EXECUTED_SQL)


# ── (8) 授權：無 token → 401 ────────────────────────────────────────


def test_lookup_requires_auth(client: TestClient) -> None:
    res = client.get("/dict/lookup?w=anything")
    assert res.status_code == 401
    assert res.json()["ok"] is False
    assert res.json()["error"]["code"] == "unauthorized"


# ── (9) helper 層級守門：^[a-z]+$ 之外的輸入直接 None ──────────────


def test_synthesize_word_audio_rejects_non_single_word(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dict_audio, "_synthesize", lambda w, model: b"fake")
    monkeypatch.setattr(dict_audio, "_publish", _async_fn(lambda w, data, ct: "https://x/y.wav"))

    assert asyncio.run(dict_audio.synthesize_word_audio("hello world")) is None
    assert asyncio.run(dict_audio.synthesize_word_audio("hello-world")) is None
    assert asyncio.run(dict_audio.synthesize_word_audio("hello123")) is None
    assert asyncio.run(dict_audio.synthesize_word_audio("Hello")) is None  # 大寫不符
    # 合 ^[a-z]+$ → 走 helper 流程
    assert asyncio.run(dict_audio.synthesize_word_audio("hello")) == "https://x/y.wav"


def test_synthesize_word_audio_handles_synth_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """底層 _synthesize 拋例外時，helper 必須降級回 None（不外拋污染路由）。"""

    def _boom(word: str, model: str) -> bytes:
        raise RuntimeError("piper not installed")

    monkeypatch.setattr(dict_audio, "_synthesize", _boom)

    assert asyncio.run(dict_audio.synthesize_word_audio("hello")) is None


def test_synthesize_word_audio_handles_publish_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """_publish 回 None（無 R2 也無本地 dir）時 helper 仍降級回 None。"""

    async def _publish_none(word: str, data: bytes, ct: str) -> str | None:
        return None

    monkeypatch.setattr(dict_audio, "_synthesize", lambda w, model: b"fake")
    monkeypatch.setattr(dict_audio, "_publish", _publish_none)

    assert asyncio.run(dict_audio.synthesize_word_audio("hello")) is None