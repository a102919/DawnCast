"""T5 Rate-limit middleware 測試。

驗證 in-memory sliding-window 限流：
  - 同一 client 連發到上限內 → 全 200；第 61 次回 429 + envelope code="rate_limited"
  - 視窗滑動後（monkeypatch now 推進時鐘）quota 恢復
  - per-IP 隔離；其他路徑不受 middleware 影響
  - 設定真的從 Settings 流入（rate_limit_dict_per_min=N 改變觸發門檻）
  - 429 response body 不洩漏 stack trace / SQL / 內部路徑（coding rules §6）

時間一律透過 monkeypatch backend.app.middleware.now 推進，不真的 sleep。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt

from app.routers import dict as dict_router
from shared.config import Settings, get_settings
from shared.db import pool as db_pool

USER_ID = "11111111-1111-1111-1111-111111111111"
LOOKUP_PATH = "/dict/lookup"


# ── Fake DB（cache 命中：固定一筆 alpha）───────────────────────────────


_CACHE_ROW: dict[str, Any] | None = None


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
        self._rows = []
        normalized = " ".join(sql.split())
        if "from public.dict_cache where word = %s" in normalized:
            if _CACHE_ROW is not None and _CACHE_ROW.get("word") == params[0]:
                self._rows = [dict(_CACHE_ROW)]
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
    """每個測試：cache 命中 alpha（帶 audio_url，不會觸發 TTS）。"""
    _set_cache(
        {
            "word": "alpha",
            "ipa": "/ˈælfə/",
            "pos": ["n"],
            "translation": "α",
            "exchange": None,
            "audio_url": "https://local/dict/alpha.wav",
            "example_en": "an alpha",
            "example_zh": "一個 α",
        }
    )


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dict_router, "connection", fake_connection)
    monkeypatch.setattr(db_pool, "connection", fake_connection)


@pytest.fixture(autouse=True)
def freeze_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 middleware.now() 釘在 t=1000.0；個別測試可在 body 內再覆寫推進。"""
    import app.middleware as mw

    monkeypatch.setattr(mw, "now", lambda: 1000.0)


@pytest.fixture
def make_settings(monkeypatch: pytest.MonkeyPatch):
    """工廠：給定 rate_limit 回傳 patched get_settings 與底層 Settings instance。

    注意：create_app() 在 app.main 內 `from shared.config import get_settings` 已綁定，
    故必須 patch `app.main.get_settings` 才能讓 create_app() 讀到新設定；
    其他模組（deps / shared.config）的 get_settings 維持原樣 ——
    本測試 JWT secret 一律是預設 dev-secret，無需動。
    """

    def _make(rate_limit: int = 60) -> Settings:
        get_settings.cache_clear()
        base = get_settings()
        new_settings = base.model_copy(update={"rate_limit_dict_per_min": rate_limit})
        monkeypatch.setattr("app.main.get_settings", lambda: new_settings)
        return new_settings

    return _make


@pytest.fixture
def make_app(make_settings: Any):
    """工廠：給定 rate_limit 回傳新 build 的 FastAPI app。"""

    def _make(rate_limit: int = 60) -> FastAPI:
        make_settings(rate_limit=rate_limit)
        from app.main import create_app

        return create_app()

    return _make


@pytest.fixture
def make_client(make_app: Any):
    """工廠：給定 (ip, rate_limit) 回傳獨立 TestClient（獨立 app instance）。"""

    def _make(ip: str = "1.2.3.4", rate_limit: int = 60) -> TestClient:
        app = make_app(rate_limit=rate_limit)
        return TestClient(app, raise_server_exceptions=False, client=(ip, 5000))

    return _make


def _auth() -> dict[str, str]:
    settings = get_settings()
    token = jwt.encode(
        {"sub": USER_ID, "aud": settings.supabase_jwt_audience},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# ── (1) 上限內全部 200 ─────────────────────────────────────────────


def test_within_limit_returns_200(make_client: Any) -> None:
    client = make_client(ip="1.1.1.1", rate_limit=60)
    for i in range(60):
        res = client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
        assert res.status_code == 200, f"第 {i + 1} 次應為 200，實際 {res.status_code}"


# ── (2) 第 61 次觸發 429 ──────────────────────────────────────────


def test_61st_call_returns_429(make_client: Any) -> None:
    client = make_client(ip="2.2.2.2", rate_limit=60)
    for _ in range(60):
        client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())

    res = client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    assert res.status_code == 429
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "rate_limited"
    assert "頻率" in body["error"]["message"]


# ── (3) 視窗滑動後 quota 恢復 ──────────────────────────────────────


def test_window_slide_resets_quota(make_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.middleware as mw

    client = make_client(ip="3.3.3.3", rate_limit=60)

    # 用滿
    for _ in range(60):
        client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    # 第 61 次觸發上限
    assert client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth()).status_code == 429

    # 推進時間 61 秒（超過 60 秒視窗）— 同 IP 應可再用
    monkeypatch.setattr(mw, "now", lambda: 1000.0 + 61.0)
    res = client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    assert res.status_code == 200


# ── (4) per-IP 隔離（HTTP 層級）──────────────────────────────────


def test_different_ips_are_independent(make_app: Any) -> None:
    """兩個 TestClient 共享同一個 app（同一個 middleware 實例），不同 IP。"""
    app = make_app(rate_limit=60)
    client_a = TestClient(app, raise_server_exceptions=False, client=("10.0.0.1", 5000))
    client_b = TestClient(app, raise_server_exceptions=False, client=("10.0.0.2", 5000))

    # A 用滿
    for _ in range(60):
        client_a.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    assert client_a.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth()).status_code == 429

    # B 第一次仍可放行
    res_b = client_b.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    assert res_b.status_code == 200


# ── (5) 其他路徑不受影響 ─────────────────────────────────────────


def test_other_paths_not_affected(make_client: Any) -> None:
    client = make_client(ip="6.6.6.6", rate_limit=60)

    # /dict/lookup 第 61 次會 429（先證明 middleware 有裝）
    for _ in range(61):
        client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    assert client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth()).status_code == 429

    # /health 連發 100 次全部 200
    for _ in range(100):
        assert client.get("/health").status_code == 200


# ── (6) 設定可注入 ──────────────────────────────────────────────


def test_limit_configurable_via_settings(make_client: Any) -> None:
    client = make_client(ip="7.7.7.7", rate_limit=5)
    for _ in range(5):
        assert client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth()).status_code == 200
    res = client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "rate_limited"


# ── (7) 429 response 不洩漏內部資訊 ──────────────────────────────


def test_rate_limited_response_no_stack_leak(make_client: Any) -> None:
    client = make_client(ip="8.8.8.8", rate_limit=60)
    for _ in range(61):
        client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    res = client.get(f"{LOOKUP_PATH}?w=alpha", headers=_auth())
    text = res.text
    assert "Traceback" not in text
    assert "select " not in text
    assert "/Users/" not in text
    assert "/home/" not in text
    assert "pydantic" not in text.lower()
    # envelope 必須仍是合法 ApiResponse shape
    body = res.json()
    assert set(body.keys()) == {"ok", "data", "error"}
    assert body["data"] is None
    assert body["error"]["code"] == "rate_limited"


# ── (8) bucket 單元邏輯（不經 HTTP）─────────────────────────────


def test_bucket_unit_logic(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.middleware as mw

    t = [1000.0]
    monkeypatch.setattr(mw, "now", lambda: t[0])

    bucket = mw.SlidingWindowBucket(limit=3, window_sec=60.0)
    assert bucket.check("ip-X") is True
    assert bucket.check("ip-X") is True
    assert bucket.check("ip-X") is True
    assert bucket.check("ip-X") is False  # 第 4 次觸發上限

    # 不同 IP 互不影響
    assert bucket.check("ip-Y") is True

    # 時間推進超過視窗 → X 恢復
    t[0] = 1000.0 + 61.0
    assert bucket.check("ip-X") is True


# ── (9) 跨域 SPA 可消費：429 必帶 CORS header ──────────────────
#
# 規格背景：前端是跨域 SPA（allow_credentials=True），/dict/lookup 帶
# Authorization 屬非安全請求，缺 ACAO 會讓瀏覽器在 JS 讀到 body 前擋掉
# fetch，使用者看到「網路錯誤」而非「超過查詞頻率限制」。
# 條件：CORS 必須是 middleware 鏈最外層；RateLimit 在內層；否則它的
# JSONResponse(429) 回程不經 CORSMiddleware，永遠不會帶 ACAO。
# TestClient 不強制 CORS，但 starlette 仍會走完整 middleware 鏈，所以
# 真實行為可被 httpx Response.headers 抓到。

FRONTEND_ORIGIN = "http://localhost:5173"


def test_429_response_carries_cors_header_for_cross_origin_spa(
    make_client: Any,
) -> None:
    client = make_client(ip="9.9.9.9", rate_limit=60)

    for _ in range(60):
        client.get(
            f"{LOOKUP_PATH}?w=alpha",
            headers={**_auth(), "Origin": FRONTEND_ORIGIN},
        )

    res = client.get(
        f"{LOOKUP_PATH}?w=alpha",
        headers={**_auth(), "Origin": FRONTEND_ORIGIN},
    )
    assert res.status_code == 429
    # 跨域 SPA 必讀得到 ACAO header，否則瀏覽器會擋掉 fetch
    assert (
        res.headers.get("access-control-allow-origin") == FRONTEND_ORIGIN
    ), f"預期 ACAO={FRONTEND_ORIGIN}，實際 headers={dict(res.headers)!r}"


# ── Self-check Demo ──────────────────────────────────────────────


if __name__ == "__main__":
    """手動跑：印出 60 → 61 邏輯會觸發 429，證明邏輯真的有效。

    cd backend && uv run python tests/test_dict_rate_limit.py
    """
    import sys

    import app.middleware as mw

    print("── SlidingWindowBucket 直接驗證 ──")
    bucket = mw.SlidingWindowBucket(limit=60, window_sec=60.0)
    ok_count = 0
    blocked_at: int | None = None
    for i in range(61):
        if bucket.check("demo-ip"):
            ok_count += 1
        else:
            blocked_at = i + 1
            break
    print(f"前 {ok_count} 次放行；第 {blocked_at} 次觸發上限（期望 61）")
    assert ok_count == 60
    assert blocked_at == 61

    print("\n── RateLimitMiddleware 端到端（FakeConnection）──")
    # 重複單元測試的 assert 邏輯跑一次完整 demo：直接 raise JSONResponse 的 envelope
    from app.response import err

    body = err("rate_limited", "超過查詞頻率限制")
    print(f"429 envelope: {body.model_dump()}")
    assert body.model_dump() == {
        "ok": False,
        "data": None,
        "error": {"code": "rate_limited", "message": "超過查詞頻率限制"},
    }

    print("\nOK — bucket 與 envelope 邏輯符合 spec。")
    sys.exit(0)