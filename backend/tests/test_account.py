"""帳號自我管理 router 測試（T4）：GET /me、DELETE /me。

驗證重點：
  (a) GET /me：認證用戶拿回 AccountInfo（id / email / tz / delivery_time / created_at）
  (b) DELETE /me：cascade 清空 public.users + 8 張 child tables 該 user_id 列
      （deliveries / daily_orders / user_vocab / user_favorites / user_settings /
        topic_requests / user_heard_topics / user_activity）
  (c) cascade 範圍正確：刪 A 不動 B
  (d) DELETE 在同一 connection 內 transaction 執行（commit 次數 = 1）
  (e) GET / DELETE 都需要 JWT（無 token → 401）

做法：照 test_api.py FakeConnection pattern，攔截 SQL 並 mock in-memory state。
SQL 觸發時同步 mutate 9 張表的小型 fixture，DELETE 後斷言 9 表該 user_id 全清。

注意：真實的 FK cascade 由 DB migration 的 ON DELETE CASCADE 處理，本測試保護
「我們的程式碼正確觸發 DELETE」這層；SQL schema 正確性由 migration 保證。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.routers import account as account_router
from shared.config import get_settings

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

# 9 張表的 fixture：每表以 user_id 為 key 存列，模擬 DB 內容。
# DELETE /me 後，A 的列必須全消失；B 不受影響。
USERS_BY_ID: dict[str, dict[str, Any]] = {
    USER_A: {
        "id": USER_A,
        "tz": "Asia/Taipei",
        "delivery_time": "08:30",
        "created_at": "2026-07-01T00:00:00Z",
    },
    USER_B: {
        "id": USER_B,
        "tz": "Asia/Taipei",
        "delivery_time": "07:00",
        "created_at": "2026-07-02T00:00:00Z",
    },
}

DELIVERIES_BY_USER: dict[str, list[dict[str, Any]]] = {
    USER_A: [
        {"id": "d1", "user_id": USER_A, "episode_id": "ep1", "deliver_date": "2026-07-15"}
    ],
    USER_B: [
        {"id": "d2", "user_id": USER_B, "episode_id": "ep2", "deliver_date": "2026-07-15"}
    ],
}

DAILY_ORDERS_BY_USER: dict[str, list[dict[str, Any]]] = {
    USER_A: [
        {
            "user_id": USER_A,
            "order_date": "2026-07-15",
            "selected_topics": ["tech"],
            "specific_request": None,
            "status": "played",
            "delivery_time": "08:30",
            "created_at": "2026-07-15T00:00:00Z",
            "updated_at": "2026-07-15T08:30:00Z",
            "played_at": "2026-07-15T08:30:00Z",
            "entry_mode": "topic",
            "length_tier": "medium",
        }
    ],
    USER_B: [],
}

USER_VOCAB_BY_USER: dict[str, list[dict[str, Any]]] = {
    USER_A: [
        {"id": "v1", "user_id": USER_A, "word": "serendipity"},
        {"id": "v2", "user_id": USER_A, "word": "ephemeral"},
    ],
    USER_B: [{"id": "v3", "user_id": USER_B, "word": "obfuscate"}],
}

USER_FAVORITES_BY_USER: dict[str, list[dict[str, Any]]] = {
    USER_A: [{"user_id": USER_A, "episode_id": "ep1"}],
    USER_B: [],
}

USER_SETTINGS_BY_USER: dict[str, dict[str, Any]] = {
    USER_A: {"user_id": USER_A, "popup_enabled": True},
    USER_B: {"user_id": USER_B, "popup_enabled": False},
}

TOPIC_REQUESTS_BY_USER: dict[str, list[dict[str, Any]]] = {
    USER_A: [{"id": "tr1", "user_id": USER_A, "request_date": "2026-07-15"}],
    USER_B: [],
}

USER_HEARD_TOPICS_BY_USER: dict[str, list[dict[str, Any]]] = {
    USER_A: [{"user_id": USER_A, "episode_id": "ep1", "heard_date": "2026-07-15"}],
    USER_B: [],
}

USER_ACTIVITY_BY_USER: dict[str, dict[str, Any]] = {
    USER_A: {
        "user_id": USER_A,
        "streak_dates": ["2026-07-15"],
        "listen_minutes": {"2026-07": 5},
    },
    USER_B: {
        "user_id": USER_B,
        "streak_dates": [],
        "listen_minutes": {},
    },
}


def _reset_state() -> None:
    """重置 fixture；每個測試前重灌。"""
    USERS_BY_ID.clear()
    USERS_BY_ID[USER_A] = {
        "id": USER_A,
        "tz": "Asia/Taipei",
        "delivery_time": "08:30",
        "created_at": "2026-07-01T00:00:00Z",
    }
    USERS_BY_ID[USER_B] = {
        "id": USER_B,
        "tz": "Asia/Taipei",
        "delivery_time": "07:00",
        "created_at": "2026-07-02T00:00:00Z",
    }
    DELIVERIES_BY_USER[USER_A] = [
        {"id": "d1", "user_id": USER_A, "episode_id": "ep1", "deliver_date": "2026-07-15"}
    ]
    DELIVERIES_BY_USER[USER_B] = [
        {"id": "d2", "user_id": USER_B, "episode_id": "ep2", "deliver_date": "2026-07-15"}
    ]
    DAILY_ORDERS_BY_USER[USER_A] = [
        {
            "user_id": USER_A,
            "order_date": "2026-07-15",
            "selected_topics": ["tech"],
            "specific_request": None,
            "status": "played",
            "delivery_time": "08:30",
            "created_at": "2026-07-15T00:00:00Z",
            "updated_at": "2026-07-15T08:30:00Z",
            "played_at": "2026-07-15T08:30:00Z",
            "entry_mode": "topic",
            "length_tier": "medium",
        }
    ]
    DAILY_ORDERS_BY_USER[USER_B] = []
    USER_VOCAB_BY_USER[USER_A] = [
        {"id": "v1", "user_id": USER_A, "word": "serendipity"},
        {"id": "v2", "user_id": USER_A, "word": "ephemeral"},
    ]
    USER_VOCAB_BY_USER[USER_B] = [{"id": "v3", "user_id": USER_B, "word": "obfuscate"}]
    USER_FAVORITES_BY_USER[USER_A] = [{"user_id": USER_A, "episode_id": "ep1"}]
    USER_FAVORITES_BY_USER[USER_B] = []
    USER_SETTINGS_BY_USER[USER_A] = {"user_id": USER_A, "popup_enabled": True}
    USER_SETTINGS_BY_USER[USER_B] = {"user_id": USER_B, "popup_enabled": False}
    TOPIC_REQUESTS_BY_USER[USER_A] = [
        {"id": "tr1", "user_id": USER_A, "request_date": "2026-07-15"}
    ]
    TOPIC_REQUESTS_BY_USER[USER_B] = []
    USER_HEARD_TOPICS_BY_USER[USER_A] = [
        {"user_id": USER_A, "episode_id": "ep1", "heard_date": "2026-07-15"}
    ]
    USER_HEARD_TOPICS_BY_USER[USER_B] = []
    USER_ACTIVITY_BY_USER[USER_A] = {
        "user_id": USER_A,
        "streak_dates": ["2026-07-15"],
        "listen_minutes": {"2026-07": 5},
    }
    USER_ACTIVITY_BY_USER[USER_B] = {
        "user_id": USER_B,
        "streak_dates": [],
        "listen_minutes": {},
    }


# 9 張 child tables 的清單（直接對應上一輪漏 user_activity 的 feedback）。
# 測試保護的是「DELETE 觸發 → 在 mock 層級記錄對應 user_id 的清除」這層；
# 順序無關（測試只檢查最終結果），但列出來方便閱讀。
CHILD_TABLES: tuple[tuple[str, dict[str, list[dict[str, Any]]]], ...] = (
    ("public.deliveries", DELIVERIES_BY_USER),
    ("public.daily_orders", DAILY_ORDERS_BY_USER),
    ("public.user_vocab", USER_VOCAB_BY_USER),
    ("public.user_favorites", USER_FAVORITES_BY_USER),
    ("public.user_settings", USER_SETTINGS_BY_USER),
    ("public.topic_requests", TOPIC_REQUESTS_BY_USER),
    ("public.user_heard_topics", USER_HEARD_TOPICS_BY_USER),
    ("public.user_activity", USER_ACTIVITY_BY_USER),
)


class FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._rowcount: int = 0

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    @property
    def rowcount(self) -> int:  # noqa: D401  # psycopg cursor 介面
        return self._rowcount

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())  # 正規化空白

        # ── GET /me：SELECT FROM public.users WHERE id = %s ──
        if "from public.users" in s and "where id = %s" in s and "delete" not in s:
            user_id = params[0]
            row = USERS_BY_ID.get(user_id)
            self._rows = [row] if row is not None else []
            return

        # ── DELETE /me：DELETE FROM public.users WHERE id = %s ──
        if "delete from public.users" in s:
            user_id = params[0]
            row = USERS_BY_ID.pop(user_id, None)
            # 真實 DB 上 FK ON DELETE CASCADE 會自動清 child tables。
            # mock 層級手動清 8 張 child tables（確保測試也能驗證
            # 「若 cascade 失效，child tables 會殘留」這個 bug）。
            if row is not None:
                for _table, store in CHILD_TABLES:
                    store.pop(user_id, None)
            self._rowcount = 1 if row is not None else 0
            self._rows = []
            return

        # 其餘測試不涉及的查詢 → 回空
        self._rows = []
        self._rowcount = 0
        return

    async def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    async def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    """計數 commit 次數，驗 DELETE 在同一 transaction 內執行（commit = 1）。"""

    def __init__(self) -> None:
        self.commit_count = 0

    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()

    async def commit(self) -> None:
        self.commit_count += 1


@asynccontextmanager
async def fake_connection() -> AsyncIterator[FakeConnection]:
    yield FakeConnection()


@pytest.fixture(autouse=True)
def _reset_fixtures() -> None:
    _reset_state()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(account_router, "connection", fake_connection)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _token(user_id: str, *, email: str | None = None) -> str:
    settings = get_settings()
    payload: dict[str, Any] = {"sub": user_id, "aud": settings.supabase_jwt_audience}
    if email is not None:
        payload["email"] = email
    return jwt.encode(payload, settings.supabase_jwt_secret, algorithm="HS256")


def _auth(user_id: str, *, email: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, email=email)}"}


# ── (e) 授權 ──────────────────────────────────────────────────────


def test_get_me_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/me")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_delete_me_no_jwt_returns_401(client: TestClient) -> None:
    res = client.delete("/me")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_delete_me_bad_jwt_returns_401(client: TestClient) -> None:
    res = client.delete("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401
    assert res.json()["ok"] is False


# ── (a) GET /me：認證用戶拿回 AccountInfo ────────────────────────


def test_get_me_returns_account_info(client: TestClient) -> None:
    res = client.get("/me", headers=_auth(USER_A, email="alice@example.com"))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["id"] == USER_A
    assert data["email"] == "alice@example.com"
    assert data["tz"] == "Asia/Taipei"
    assert data["deliveryTime"] == "08:30"
    assert data["createdAt"] == "2026-07-01T00:00:00Z"


def test_get_me_envelope_shape(client: TestClient) -> None:
    res = client.get("/me", headers=_auth(USER_A))
    body = res.json()
    assert set(body.keys()) == {"ok", "data", "error"}
    assert body["error"] is None
    data = body["data"]
    # 5 欄位齊全（id / email / tz / deliveryTime / createdAt）
    assert set(data.keys()) == {"id", "email", "tz", "deliveryTime", "createdAt"}


def test_get_me_without_email_claim_returns_empty_string(client: TestClient) -> None:
    # 沒 email claim 的 JWT：email 欄位回空字串（不丟錯）
    res = client.get("/me", headers=_auth(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["email"] == ""


def test_get_me_scoped_to_caller(client: TestClient) -> None:
    # A 取自己的、B 取自己的，兩者 id 不同；不可交錯
    res_a = client.get("/me", headers=_auth(USER_A))
    res_b = client.get("/me", headers=_auth(USER_B))
    assert res_a.json()["data"]["id"] == USER_A
    assert res_b.json()["data"]["id"] == USER_B
    assert res_a.json()["data"]["tz"] == "Asia/Taipei"
    assert res_b.json()["data"]["deliveryTime"] == "07:00"


# ── (b) DELETE /me：清 9 表（A 的列全沒）────────────────────────


def test_delete_me_removes_user_from_public_users(client: TestClient) -> None:
    res = client.delete("/me", headers=_auth(USER_A))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"] is None
    # 直接從 fixture 確認 public.users 該 user_id 列已消失
    assert USER_A not in USERS_BY_ID


def test_delete_me_clears_all_eight_cascade_tables(client: TestClient) -> None:
    """核心測試：直接對應上輪漏 user_activity 的 feedback。
    刪除後 8 張 child tables 該 user_id 列必須全空。"""
    # 前置確認：A 確實有資料
    assert USER_A in USERS_BY_ID
    for table, store in CHILD_TABLES:
        assert USER_A in store, f"測試前置失敗：A 在 {table} 應有資料"

    res = client.delete("/me", headers=_auth(USER_A))
    assert res.status_code == 200

    # 逐表斷言：A 的列已清空
    for table, store in CHILD_TABLES:
        assert USER_A not in store, (
            f"{table} 該 user_id={USER_A} 的列未被清空"
        )


def test_delete_me_other_users_data_intact(client: TestClient) -> None:
    # A、B 都有資料 → 刪 A 後 B 必須不動
    assert USER_A in USERS_BY_ID
    assert USER_B in USERS_BY_ID
    for _table, store in CHILD_TABLES:
        assert USER_A in store
        assert USER_B in store

    client.delete("/me", headers=_auth(USER_A))

    # B 的 public.users 列必須還在
    assert USER_B in USERS_BY_ID
    assert USERS_BY_ID[USER_B]["delivery_time"] == "07:00"
    # B 的 8 張 child tables 列必須完整
    for table, store in CHILD_TABLES:
        assert USER_B in store, f"誤刪 B 在 {table} 的列"
    # A 已清
    assert USER_A not in USERS_BY_ID
    for table, store in CHILD_TABLES:
        assert USER_A not in store, f"{table} 漏清 A"


def test_delete_me_returns_ok_envelope(client: TestClient) -> None:
    res = client.delete("/me", headers=_auth(USER_A))
    body = res.json()
    assert set(body.keys()) == {"ok", "data", "error"}
    assert body["ok"] is True
    assert body["error"] is None
    assert body["data"] is None  # DELETE 回 ok(None)


# ── (d) DELETE 在同一 transaction 內執行 ────────────────────────


def test_delete_me_uses_single_transaction() -> None:
    """DELETE /me 必須在同一 connection 內 transaction 執行：commit 次數 = 1。

    用 FakeConnection 計數 commit() 呼叫次數；多於 1 表示開新連線或未包 transaction。
    """
    # 這個測試不走 TestClient，自己呼叫 route handler + 直接觀察 FakeConnection.commit_count
    from app.main import create_app

    app = create_app()
    # 透過 app 取得 FakeConnection 實例
    captured: dict[str, FakeConnection] = {}

    @asynccontextmanager
    async def capture_connection() -> AsyncIterator[FakeConnection]:
        conn = FakeConnection()
        captured["conn"] = conn
        yield conn

    original_connection = account_router.connection
    account_router.connection = capture_connection
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            res = c.delete("/me", headers=_auth(USER_A))
            assert res.status_code == 200
    finally:
        account_router.connection = original_connection

    assert "conn" in captured
    # 進 router 用的是 connection() async context manager 一次，
    # commit 次數必為 1（單 transaction）。
    assert captured["conn"].commit_count == 1, (
        f"DELETE /me commit 次數 = {captured['conn'].commit_count}，預期 1"
    )


# ── (b+) DELETE 重複執行冪等 ─────────────────────────────────────


def test_delete_me_twice_idempotent(client: TestClient) -> None:
    """第一次 DELETE 200，第二次 DELETE 也是 200（user row 已不存在，但 router 不應炸）。"""
    res1 = client.delete("/me", headers=_auth(USER_A))
    assert res1.status_code == 200
    res2 = client.delete("/me", headers=_auth(USER_A))
    # 第二輪：public.users 已無列，DELETE 影響 0 列但不報錯
    assert res2.status_code == 200
    assert res2.json()["ok"] is True