"""Settings router 測試（T9）：getSettings / updateSettings / resetPopupPreferences。

驗證重點：
  (a) 無 JWT → 401（三個 endpoint 全驗）
  (b) happy path：無列回 Settings() 預設值；有列回列值；PATCH 套用部分更新；
      reset-popup 強制把 popup 欄位壓回 (true, false)
  (c) 授權收斂：A 的 token 只看到 A 的設定，拿不到 B 的；若 PATCH 路由漏
      where user_id = %s 也只會寫入自己的 user_id，不會覆蓋到別人

做法：照 test_api.py FakeConnection pattern，攔截 SQL 並以 in-memory state 模擬
user_settings。並不重新實作 DB 行為，只驗 router 對 user_id 的收斂。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.routers import settings as settings_router
from shared.config import get_settings

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

# user_id → user_settings 列（None = 該 user 還沒列，會走 Settings() 預設）
SETTINGS_BY_USER: dict[str, dict[str, Any] | None] = {
    USER_A: {
        "popup_enabled": True,
        "popup_dont_show_again": False,
        "playback_rate": 1.0,
        "font_size": "md",
        "theme": "auto",
        "preferred_topics": ["tech"],
        "default_delivery_time": "07:00",
    },
    USER_B: {
        "popup_enabled": False,
        "popup_dont_show_again": True,
        "playback_rate": 1.5,
        "font_size": "lg",
        "theme": "dark",
        "preferred_topics": ["news"],
        "default_delivery_time": "21:30",
    },
}


def _reset_state() -> None:
    SETTINGS_BY_USER[USER_A] = {
        "popup_enabled": True,
        "popup_dont_show_again": False,
        "playback_rate": 1.0,
        "font_size": "md",
        "theme": "auto",
        "preferred_topics": ["tech"],
        "default_delivery_time": "07:00",
    }
    SETTINGS_BY_USER[USER_B] = {
        "popup_enabled": False,
        "popup_dont_show_again": True,
        "playback_rate": 1.5,
        "font_size": "lg",
        "theme": "dark",
        "preferred_topics": ["news"],
        "default_delivery_time": "21:30",
    }


class FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())

        # SELECT from public.user_settings where user_id = %s（GET 與 PATCH 後的回讀）
        if (
            "from public.user_settings" in s
            and "where user_id = %s" in s
            and "insert" not in s
        ):
            user_id = params[0]
            row = SETTINGS_BY_USER.get(user_id)
            self._rows = [row] if row else []
            return

        # upsert（PATCH）：15 個 params，patch 為 None 時沿用既有值
        if "insert into public.user_settings" in s:
            # reset-popup（1 個 param，SQL 內含 literal '= true' / '= false'）：
            # 把 popup 欄位硬壓回 (True, False)
            if "popup_enabled = true, popup_dont_show_again = false" in s:
                user_id = params[0]
                existing = SETTINGS_BY_USER.get(user_id) or {}
                SETTINGS_BY_USER[user_id] = {
                    "popup_enabled": True,
                    "popup_dont_show_again": False,
                    "playback_rate": existing.get("playback_rate", 1.0),
                    "font_size": existing.get("font_size", "md"),
                    "theme": existing.get("theme", "auto"),
                    "preferred_topics": existing.get("preferred_topics", []),
                    "default_delivery_time": existing.get(
                        "default_delivery_time", "07:00"
                    ),
                }
                self._rows = []
                return

            # PATCH upsert（15 個 params）：patch 為 None 時沿用既有值。
            # params 排列：(user_id, popup_enabled, popup_dont_show_again,
            # playback_rate, font_size, theme, topics_json,
            # default_delivery_time, *同樣 7 個再給 ON CONFLICT*)
            user_id = params[0]
            existing = SETTINGS_BY_USER.get(user_id) or {}
            merged: dict[str, Any] = {
                "popup_enabled": params[1]
                if params[1] is not None
                else existing.get("popup_enabled", True),
                "popup_dont_show_again": params[2]
                if params[2] is not None
                else existing.get("popup_dont_show_again", False),
                "playback_rate": params[3]
                if params[3] is not None
                else existing.get("playback_rate", 1.0),
                "font_size": params[4]
                if params[4] is not None
                else existing.get("font_size", "md"),
                "theme": params[5]
                if params[5] is not None
                else existing.get("theme", "auto"),
                "preferred_topics": json.loads(params[6])
                if params[6] is not None
                else existing.get("preferred_topics", []),
                "default_delivery_time": params[7]
                if params[7] is not None
                else existing.get("default_delivery_time", "07:00"),
            }
            SETTINGS_BY_USER[user_id] = merged
            self._rows = []
            return

        self._rows = []
        return

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()

    async def commit(self) -> None:
        return None


@asynccontextmanager
async def fake_connection() -> AsyncIterator[FakeConnection]:
    yield FakeConnection()


@pytest.fixture(autouse=True)
def _reset_fixtures() -> None:
    _reset_state()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_router, "connection", fake_connection)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _token(user_id: str) -> str:
    cfg = get_settings()
    return jwt.encode(
        {"sub": user_id, "aud": cfg.supabase_jwt_audience},
        cfg.supabase_jwt_secret,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


# ── (a) 無 JWT → 401（三 endpoint）─────────────────────────────


def test_get_settings_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/settings")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_patch_settings_no_jwt_returns_401(client: TestClient) -> None:
    res = client.patch("/settings", json={"playbackRate": 1.5})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_reset_popup_no_jwt_returns_401(client: TestClient) -> None:
    res = client.post("/settings/reset-popup")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


# ── (b) happy path ────────────────────────────────────────────


def test_get_settings_returns_stored_row(client: TestClient) -> None:
    res = client.get("/settings", headers=_auth(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["playbackRate"] == 1.0
    assert data["preferredTopics"] == ["tech"]
    assert data["defaultDeliveryTime"] == "07:00"


def test_get_settings_returns_defaults_when_no_row(client: TestClient) -> None:
    # 暫時拔掉 USER_A 的列 → router 端應走 Settings() 預設工廠
    SETTINGS_BY_USER[USER_A] = None
    res = client.get("/settings", headers=_auth(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    # 對齊 Settings() 預設值（CamelModel alias 序列化為 camelCase）
    assert data["popupEnabled"] is True
    assert data["popupDontShowAgain"] is False
    assert data["playbackRate"] == 1.0
    assert data["fontSize"] == "md"
    assert data["theme"] == "auto"
    assert data["preferredTopics"] == []
    assert data["defaultDeliveryTime"] == "07:00"


def test_patch_settings_partial_update_only_touches_given_fields(
    client: TestClient,
) -> None:
    res = client.patch(
        "/settings",
        json={"playbackRate": 2.0, "theme": "dark"},
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    # 有給的欄位更新
    assert data["playbackRate"] == 2.0
    assert data["theme"] == "dark"
    # 沒給的欄位保持原值
    assert data["defaultDeliveryTime"] == "07:00"
    assert data["preferredTopics"] == ["tech"]
    assert data["popupEnabled"] is True


def test_reset_popup_sets_enabled_true_and_dont_show_false(
    client: TestClient,
) -> None:
    res = client.post("/settings/reset-popup", headers=_auth(USER_B))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    row = SETTINGS_BY_USER[USER_B]
    assert row is not None
    assert row["popup_enabled"] is True
    assert row["popup_dont_show_again"] is False
    # 其他欄位保持不動（reset-popup 只動兩個 bool）
    assert row["playback_rate"] == 1.5


# ── (c) 授權收斂：A 拿不到 B 的設定 ────────────────────────────


def test_get_settings_scoped_to_owner(client: TestClient) -> None:
    res_a = client.get("/settings", headers=_auth(USER_A))
    res_b = client.get("/settings", headers=_auth(USER_B))
    assert res_a.status_code == 200
    assert res_b.status_code == 200
    data_a = res_a.json()["data"]
    data_b = res_b.json()["data"]
    # A 的 playbackRate = 1.0；B = 1.5。拿錯就代表 where user_id = %s 漏寫
    assert data_a["playbackRate"] == 1.0
    assert data_b["playbackRate"] == 1.5
    # preferredTopics 也要 user-scoped
    assert data_a["preferredTopics"] == ["tech"]
    assert data_b["preferredTopics"] == ["news"]


def test_patch_settings_does_not_leak_to_other_user(client: TestClient) -> None:
    client.patch("/settings", json={"playbackRate": 3.0}, headers=_auth(USER_B))
    res_a = client.get("/settings", headers=_auth(USER_A))
    data_a = res_a.json()["data"]
    # A 必須維持原值（1.0）；B 改 3.0 不應擴散
    assert data_a["playbackRate"] == 1.0
