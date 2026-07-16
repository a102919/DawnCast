"""出餐通知（T8）測試。

範圍限縮：本專案目前沒有任何 email 套件 / 憑證（查過 shared/config.py 與
.env.example 均無 SMTP/SendGrid/Resend/SES 欄位），故本次只做「到
defaultDeliveryTime → 產生待寄通知記錄」的觸發邏輯，不假造外部寄信串接。

純函式測試（should_notify / build_pending_notifications）完全不碰時鐘 / DB，
時間全部用可控 datetime 參數注入，驗證觸發邏輯本身。
另外補一段 router 授權邊界測試，沿用 test_admin.py 的 FakeConnection 模式。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.routers import admin as admin_router
from app.routers import notifications as notifications_router
from app.routers.notifications import (
    PendingNotification,
    UserDeliveryState,
    build_pending_notifications,
    should_notify,
)
from shared.config import Settings

TAIPEI = ZoneInfo("Asia/Taipei")
ADMIN_TOKEN = "test-admin-token"


# ── should_notify ────────────────────────────────────────────────


def test_should_notify_happy_path_exact_minute_match() -> None:
    now = datetime(2026, 7, 16, 7, 0, tzinfo=TAIPEI)
    assert should_notify(now, "07:00", has_delivery=True) is True


def test_should_notify_before_delivery_time() -> None:
    now = datetime(2026, 7, 16, 6, 59, tzinfo=TAIPEI)
    assert should_notify(now, "07:00", has_delivery=True) is False


def test_should_notify_after_delivery_time_not_exact_minute() -> None:
    """分鐘精確比對：時間已過但非整分命中，不應觸發（不是 >= 就一直發）。"""
    now = datetime(2026, 7, 16, 8, 0, tzinfo=TAIPEI)
    assert should_notify(now, "07:00", has_delivery=True) is False


def test_should_notify_no_delivery_yet_does_not_fire() -> None:
    """時間到但新集還沒生成 → 不誤發。"""
    now = datetime(2026, 7, 16, 7, 0, tzinfo=TAIPEI)
    assert should_notify(now, "07:00", has_delivery=False) is False


@pytest.mark.parametrize("bad_time", ["", "25:99", "not-a-time", "7:00:00:00"])
def test_should_notify_invalid_delivery_time_is_defensive(bad_time: str) -> None:
    now = datetime(2026, 7, 16, 7, 0, tzinfo=TAIPEI)
    assert should_notify(now, bad_time, has_delivery=True) is False


def test_should_notify_is_timezone_independent_of_machine_clock() -> None:
    """只看傳入 datetime 的牆鐘欄位，不依賴執行機器的本機時區 / 真實時鐘。"""
    now_taipei = datetime(2026, 7, 16, 7, 0, tzinfo=TAIPEI)
    now_utc_equivalent = now_taipei.astimezone(ZoneInfo("UTC"))
    # 兩者代表同一個世界時刻，但牆鐘欄位不同（23:00 UTC 前一天）——
    # should_notify 只看傳入物件自身的 hour/minute，不做時區轉換。
    assert should_notify(now_taipei, "07:00", has_delivery=True) is True
    assert should_notify(now_utc_equivalent, "07:00", has_delivery=True) is False


# ── build_pending_notifications ──────────────────────────────────


def test_build_pending_notifications_filters_mixed_input() -> None:
    now = datetime(2026, 7, 16, 7, 0, tzinfo=TAIPEI)
    states = [
        UserDeliveryState(user_id="user-a", delivery_time="07:00", has_delivery=True),
        UserDeliveryState(user_id="user-b", delivery_time="07:00", has_delivery=False),
        UserDeliveryState(user_id="user-c", delivery_time="08:00", has_delivery=True),
    ]
    result = build_pending_notifications(now, states)
    assert result == [PendingNotification(user_id="user-a", delivery_time="07:00")]


def test_build_pending_notifications_empty_input() -> None:
    now = datetime(2026, 7, 16, 7, 0, tzinfo=TAIPEI)
    assert build_pending_notifications(now, []) == []


# ── router 授權邊界（沿用 test_admin.py 的 FakeConnection 模式）───


_STATE_ROWS: list[dict[str, Any]] = [
    {"user_id": "user-a", "default_delivery_time": "07:00", "has_delivery": True},
    {"user_id": "user-b", "default_delivery_time": "07:00", "has_delivery": False},
]


class FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())
        if "user_settings" in s and "deliveries" in s:
            self._rows = list(_STATE_ROWS)
            return
        self._rows = []
        return

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class FakeConnection:
    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()


@asynccontextmanager
async def fake_connection() -> AsyncIterator[FakeConnection]:
    yield FakeConnection()


@pytest.fixture(autouse=True)
def patch_notifications_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifications_router, "connection", fake_connection)
    fake_settings = Settings(
        environment="dev", admin_token=ADMIN_TOKEN, app_timezone="Asia/Taipei"
    )
    monkeypatch.setattr(notifications_router, "get_settings", lambda: fake_settings)
    # require_admin_token 定義在 admin.py，用的是 admin 模組自己的 get_settings 參照，
    # 兩套授權/設定各自 patch 才會一致（比照 admin.py 既有慣例，見 admin_token 檢查）。
    monkeypatch.setattr(admin_router, "get_settings", lambda: fake_settings)
    # 固定 now，讓 happy path 測試不依賴真實時鐘。
    monkeypatch.setattr(
        notifications_router,
        "_now_taipei",
        lambda: datetime(2026, 7, 16, 7, 0, tzinfo=TAIPEI),
    )


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def test_pending_no_token_returns_401_and_skips_db(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    async def _spy_connection() -> AsyncIterator[FakeConnection]:
        nonlocal called
        called = True
        yield FakeConnection()

    monkeypatch.setattr(notifications_router, "connection", asynccontextmanager(_spy_connection))

    res = client.get("/notifications/pending")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"
    assert called is False


def test_pending_correct_token_returns_only_the_due_user(client: TestClient) -> None:
    res = client.get("/notifications/pending", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"] == [{"userId": "user-a", "deliveryTime": "07:00"}]
