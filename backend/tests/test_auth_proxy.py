"""Auth proxy router 測試：SPA 走 supabase-js SDK 打 /auth/v1/*，
proxy 透傳到 gotrue-mon:9999（去掉 /auth/v1/ prefix）。

mock httpx 不打真 gotrue。驗證重點：
  (a) /auth/v1/{path} 對應打到 gotrue-mon:9999/{path}（prefix 正確剝）
  (b) query string 保留
  (c) follow_redirects=False（gotrue 302 → accounts.google.com 直接透傳給 SPA browser）
  (d) hop-by-hop headers（Host 等）不轉發給 gotrue
  (e) Authorization / Content-Type 等真實 headers 保留
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


class _FakeUpstream:
    """fake httpx response，把上游呼叫記下來供斷言用。"""

    def __init__(self, status_code: int = 302, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {"location": "https://accounts.google.com/o/oauth2/v2/auth?x=1"}
        self.content = b""


@pytest.fixture
def captured_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """monkeypatch httpx.AsyncClient.request 把呼叫參數記下來。"""
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def request(self, **kwargs: Any) -> _FakeUpstream:
            calls.append(kwargs)
            return _FakeUpstream()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    return calls


def test_proxy_strips_prefix_and_forwards_query(
    captured_calls: list[dict[str, Any]],
) -> None:
    client = TestClient(app, follow_redirects=False)
    res = client.get(
        "/auth/v1/authorize",
        params={"provider": "google", "redirect_to": "https://dawncast.pages.dev"},
    )
    assert res.status_code == 302
    assert res.headers["location"] == "https://accounts.google.com/o/oauth2/v2/auth?x=1"

    assert len(captured_calls) == 1
    call = captured_calls[0]
    # gotrue 內網 host + port，prefix 已剝
    assert call["url"].startswith("http://service-")
    assert call["url"].endswith(":9999/authorize")
    assert call["params"]["provider"] == "google"
    assert call["params"]["redirect_to"] == "https://dawncast.pages.dev"
    assert call["method"] == "GET"
    # 302 不 follow（不然 SPA browser 拿不到 Location 跳 Google）
    assert call["follow_redirects"] is False


def test_proxy_forwards_body_and_headers(
    captured_calls: list[dict[str, Any]],
) -> None:
    client = TestClient(app, follow_redirects=False)
    res = client.post(
        "/auth/v1/token",
        headers={"authorization": "Bearer test", "content-type": "application/json"},
        json={"grant_type": "refresh_token", "refresh_token": "rt-xxx"},
    )
    assert res.status_code == 302

    call = captured_calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith(":9999/token")
    # 真實 header 保留
    assert call["headers"]["authorization"] == "Bearer test"
    assert call["headers"]["content-type"] == "application/json"
    # hop-by-hop header 不轉發
    assert "host" not in call["headers"]
    assert "content-length" not in call["headers"]
    # body 轉發
    import json as _json
    body = _json.loads(call["content"])
    assert body["grant_type"] == "refresh_token"