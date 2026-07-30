"""jobs router 測試：點餐送出後即時觸發生成（migration 0024：order_id 取代 date 當鍵）。

驗證重點：
  (a) 授權：無 JWT → 401
  (b) 狀態機：status=pending 才允許觸發 → 202 + enqueue control orchestrate
  (c) 狀態機：status=queued / played → 409，不重複 enqueue
  (d) 404：查無 daily_order → 404，不 enqueue
  (e) 併發：同 (user, order_id) 第二個並發請求 → 409（atomic conditional UPDATE）
  (f) 授權：user_id 永遠取自 JWT，不信任 path

做法：FakeConnection / FakeCursor 用 SQL 關鍵字分派，依情境回預置 in-memory
列；spy_queue.send 取代真 pgmq.send 收集呼叫。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import daily_orders as daily_orders_router
from shared.db import queue as db_queue
from shared.db import repo as db_repo
from tests._auth import auth_header
from tests._db_fakes import FakeConnection as _BaseFakeConnection
from tests._db_fakes import FakeCursor as _BaseFakeCursor
from tests._db_fakes import fake_connection

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
ORDER_ID = "cccccccc-0000-0000-0000-000000000001"
ORDER_DATE = "2026-07-16"

# 模擬 (user_id, order_id) → status；測試開始前清空，避免跨測試污染。
ORDERS: dict[tuple[str, str], str] = {}

# spy 收集 queue.send 被呼叫的 (queue_name, body)；測試中用來斷言。
SENT_MESSAGES: list[tuple[str, dict[str, Any]]] = []


class FakeCursor(_BaseFakeCursor):
    def __init__(self) -> None:
        super().__init__()
        self._last_sql: str = ""
        self._last_params: tuple[Any, ...] = ()
        self.rowcount = 0

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())  # 正規化空白
        self._last_sql = s
        self._last_params = params
        self._rows = []
        self.rowcount = 0

        # ── repo.get_order_status：SELECT status FROM daily_orders WHERE ... ──
        if "select status from public.daily_orders" in s and "where user_id = %s and id = %s" in s:
            user_id, order_id = params[0], params[1]
            status = ORDERS.get((user_id, order_id))
            if status is not None:
                self._rows = [{"status": status}]
            return

        # ── repo.get_order_date：SELECT order_date ... WHERE id = %s ──
        if "order_date" in s and "from public.daily_orders where id = %s" in s:
            (order_id,) = params
            self._rows = [{"order_date": ORDER_DATE}] if order_id == ORDER_ID else []
            return

        # ── repo.transition_order_to_queued：
        #   UPDATE daily_orders SET status='queued', updated_at=now()
        #   WHERE user_id=%s AND id=%s AND status='pending'
        if (
            "update public.daily_orders" in s
            and "status = 'queued'" in s
            and "status = 'pending'" in s
        ):
            user_id, order_id = params[0], params[1]
            current = ORDERS.get((user_id, order_id))
            if current == "pending":
                ORDERS[(user_id, order_id)] = "queued"
                self.rowcount = 1
            return

        return


class FakeConnection(_BaseFakeConnection):
    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()


async def spy_queue_send(queue: str, body: dict[str, Any]) -> int:
    """替代真 pgmq.send；收集訊息 + 回傳偽 msg_id。"""
    SENT_MESSAGES.append((queue, dict(body)))
    return len(SENT_MESSAGES)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """每個測試前清空 in-memory 狀態。"""
    ORDERS.clear()
    SENT_MESSAGES.clear()


@pytest.fixture(autouse=True)
def patch_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = fake_connection(FakeConnection)
    # jobs.py 不直接 import connection（全走 repo），只 patch db_repo 就夠。
    monkeypatch.setattr(db_repo, "connection", conn)
    # spy pgmq.send
    monkeypatch.setattr(db_queue, "send", spy_queue_send)
    # daily_orders router 也會被測試 / 共用同個 connection
    monkeypatch.setattr(daily_orders_router, "connection", conn)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


# ── (a) 授權 ────────────────────────────────────────────────────────────────


def test_no_jwt_returns_401(client: TestClient) -> None:
    res = client.post(f"/jobs/orders/{ORDER_ID}/generate")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_bad_jwt_returns_401(client: TestClient) -> None:
    res = client.post(
        f"/jobs/orders/{ORDER_ID}/generate",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


# ── (b) pending → 202 + enqueue ─────────────────────────────────────────────


def test_pending_order_returns_202_and_enqueues(client: TestClient) -> None:
    ORDERS[(USER_A, ORDER_ID)] = "pending"

    res = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res.status_code == 202
    body = res.json()
    assert body["ok"] is True
    assert body["data"] == {"orderId": ORDER_ID, "status": "queued"}
    assert body["error"] is None

    # enqueue 訊息形狀：control orchestrate + 帶 order_id/date
    assert len(SENT_MESSAGES) == 1
    queue_name, payload = SENT_MESSAGES[0]
    assert queue_name == "control"
    assert payload == {"task": "orchestrate", "order_id": ORDER_ID, "date": ORDER_DATE}

    # status 已被翻成 queued（DB 副作用）
    assert ORDERS[(USER_A, ORDER_ID)] == "queued"


# ── (c) queued / played → 409，不重複 enqueue ──────────────────────────────


def test_queued_order_returns_409(client: TestClient) -> None:
    ORDERS[(USER_A, ORDER_ID)] = "queued"

    res = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res.status_code == 409
    body = res.json()
    assert body["ok"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "conflict"

    # 沒送出新訊息
    assert SENT_MESSAGES == []


def test_played_order_returns_409(client: TestClient) -> None:
    ORDERS[(USER_A, ORDER_ID)] = "played"

    res = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "conflict"
    assert SENT_MESSAGES == []


# ── (d) 404 ────────────────────────────────────────────────────────────────


def test_missing_order_returns_404(client: TestClient) -> None:
    # ORDERS 空 → 該 user 從未下過單
    res = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res.status_code == 404
    body = res.json()
    assert body["ok"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "not_found"

    assert SENT_MESSAGES == []


# ── (e) 併發：atomic conditional UPDATE，第二個並發拿到 rowcount=0 → 409 ───


def test_concurrent_second_request_gets_409(client: TestClient) -> None:
    """模擬兩次同 (user, order_id) 依序觸發：
    第一次：pending → 翻 queued → 202
    第二次：已是 queued → rowcount=0 → 409（不 enqueue）

    對應真實 Postgres 行為：兩個 transaction 同時跑 conditional UPDATE，
    第二個會等 row lock 釋放後看到 status='queued'，rowcount=0。
    """
    ORDERS[(USER_A, ORDER_ID)] = "pending"

    res1 = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res1.status_code == 202

    res2 = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "conflict"

    # 只在第一次 enqueue，第二輪零訊息
    assert len(SENT_MESSAGES) == 1
    assert SENT_MESSAGES[0][1] == {"task": "orchestrate", "order_id": ORDER_ID, "date": ORDER_DATE}


# ── (f) 授權：user_id 永遠取自 JWT，不信任 path ─────────────────────────────


def test_auth_uses_jwt_user_not_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """user A 用自己的 token 觸發；order_id 不變，改用 spy_connection 確認
    SELECT 的第一個參數是 JWT 解析出的 user_id，不是 path 上的任何東西
    （path 本來就只帶 order_id，不帶 user_id，這裡驗證的是「即使 B 也有
    同一個 order_id 的另一筆訂單，也不會撈到 B 的」）。
    """

    seen_params: list[tuple[Any, ...]] = []

    class SpyCursor(FakeCursor):
        async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            seen_params.append(params)
            await super().execute(sql, params)

    class SpyConnection(FakeConnection):
        def cursor(self, **_: object) -> FakeCursor:
            return SpyCursor()

    @asynccontextmanager
    async def spy_connection():  # type: ignore[no-untyped-def]
        yield SpyConnection()

    monkeypatch.setattr(db_repo, "connection", spy_connection)
    # user B 確實有同一個 order_id 的訂單（如果 user_id 錯配會撈到 B 的 played）
    ORDERS[(USER_A, ORDER_ID)] = "pending"
    ORDERS[(USER_B, ORDER_ID)] = "played"  # B 的是 played，user_id 錯配會 409

    # 用 USER_A 的 token（正確所有者）
    res = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res.status_code == 202

    # 所有「(user_id, order_id) 形狀」查詢的第一個參數都應該是 USER_A。
    # get_order_date(order_id) 只帶 1 個參數（沒有 user_id 收斂，純用 id 查
    # 訊息要帶的 order_date），不屬於這條授權斷言要看的範圍，過濾掉。
    user_id_params = [p[0] for p in seen_params if len(p) == 2]
    assert user_id_params, "FakeCursor 沒收到任何 (user_id, order_id) 形狀的查詢"
    assert all(uid == USER_A for uid in user_id_params), (
        f"路由應永遠用 JWT 解析出的 user_id，實際收到 {user_id_params}"
    )


# ── 補充：404 vs 409 訊息分流（確保前端可正確分流）─────────────────────────


def test_404_message_says_no_order(client: TestClient) -> None:
    res = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res.status_code == 404
    assert "訂單" in res.json()["error"]["message"]


def test_409_message_says_already_queued(client: TestClient) -> None:
    ORDERS[(USER_A, ORDER_ID)] = "queued"
    res = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A))
    assert res.status_code == 409
    msg = res.json()["error"]["message"]
    # 訊息至少能區分 404（請先下單）vs 409（已排入/已播放），不需逐字一致
    assert msg and ("排入" in msg or "播放" in msg or "處理" in msg or "重複" in msg)


# ── 補充：envelope 形狀 ────────────────────────────────────────────────────


def test_envelope_shape_202(client: TestClient) -> None:
    ORDERS[(USER_A, ORDER_ID)] = "pending"
    body = client.post(f"/jobs/orders/{ORDER_ID}/generate", headers=auth_header(USER_A)).json()
    assert set(body.keys()) == {"ok", "data", "error"}
    assert body["ok"] is True
    assert body["error"] is None
    assert set(body["data"].keys()) == {"orderId", "status"}
