"""Daily orders router 測試（T9）：get / save / list(from,to) / markPlayed / delete / getEpisode。

驗證重點：
  (a) 無 JWT → 401（六 endpoint 全驗）
  (b) happy path：get 拿單日訂單；save upsert 整筆；list 取範圍內並按 order_date 排序；
      markPlayed 改 status='played' 並回傳；delete 移除；getEpisode 回 / null
  (c) 授權收斂：所有查詢 / 刪除都限定 owner；list 範圍 filter 不會因此跨 user 洩漏
      （若 router 漏 where user_id = %s，A 會看到 B 的訂單或刪到 B 的）

做法：照 test_api.py FakeConnection pattern，模擬 daily_orders table；/daily-orders/
{date}/episode 走 repo.find_delivered_episode，直接 patch 該函式。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import daily_orders as daily_orders_router
from shared.db import repo as db_repo
from shared.models import Episode

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

# (user_id, date) → daily_orders 內部列（snake_case 對齊 DB）。router 的 SELECT
# 用 to_char 投影；這裡以原值存，GET 階段再投影（_make_row）。
ORDERS: dict[tuple[str, str], dict[str, Any]] = {
    (USER_A, "2026-07-15"): {
        "selected_topics": ["tech"],
        "specific_request": None,
        "status": "played",
        "delivery_time": "07:00",
        "created_at": "2026-07-15T00:00:00Z",
        "updated_at": "2026-07-15T07:00:00Z",
        "played_at": "2026-07-15T07:00:00Z",
        "entry_mode": "topic",
        "length_tier": "medium",
    },
    (USER_A, "2026-07-16"): {
        "selected_topics": ["news"],
        "specific_request": None,
        "status": "pending",
        "delivery_time": "07:00",
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
        "played_at": None,
        "entry_mode": "news",
        "length_tier": "short",
    },
    (USER_B, "2026-07-15"): {
        "selected_topics": ["food"],
        "specific_request": None,
        "status": "queued",
        "delivery_time": "08:30",
        "created_at": "2026-07-15T00:00:00Z",
        "updated_at": "2026-07-15T00:00:00Z",
        "played_at": None,
        "entry_mode": "knowledge",
        "length_tier": "long",
    },
}


def _make_row(user_id: str, order_date: str, base: dict[str, Any]) -> dict[str, Any]:
    """對齊 router 的 _SELECT 投影（to_char 攤平成 date / ISO 字串欄位）。"""
    return {
        "date": order_date,
        "selected_topics": base["selected_topics"],
        "specific_request": base["specific_request"],
        "status": base["status"],
        "delivery_time": base["delivery_time"],
        "created_at": base["created_at"],
        "updated_at": base["updated_at"],
        "played_at": base["played_at"],
        "entry_mode": base["entry_mode"],
        "length_tier": base["length_tier"],
    }


def _reset_state() -> None:
    ORDERS.clear()
    ORDERS.update(
        {
            (USER_A, "2026-07-15"): {
                "selected_topics": ["tech"],
                "specific_request": None,
                "status": "played",
                "delivery_time": "07:00",
                "created_at": "2026-07-15T00:00:00Z",
                "updated_at": "2026-07-15T07:00:00Z",
                "played_at": "2026-07-15T07:00:00Z",
                "entry_mode": "topic",
                "length_tier": "medium",
            },
            (USER_A, "2026-07-16"): {
                "selected_topics": ["news"],
                "specific_request": None,
                "status": "pending",
                "delivery_time": "07:00",
                "created_at": "2026-07-16T00:00:00Z",
                "updated_at": "2026-07-16T00:00:00Z",
                "played_at": None,
                "entry_mode": "news",
                "length_tier": "short",
            },
            (USER_B, "2026-07-15"): {
                "selected_topics": ["food"],
                "specific_request": None,
                "status": "queued",
                "delivery_time": "08:30",
                "created_at": "2026-07-15T00:00:00Z",
                "updated_at": "2026-07-15T00:00:00Z",
                "played_at": None,
                "entry_mode": "knowledge",
                "length_tier": "long",
            },
        }
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
    def rowcount(self) -> int:
        return self._rowcount

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())

        # GET single：SELECT ... from public.daily_orders where user_id = %s and order_date = %s
        # 註：to_char(updated_at, ...) 內含 "update" 子串，故 UPDATE 排除用更具體的
        # "update public.daily_orders"（與下方 UPDATE 分支同一關鍵字）。
        if (
            "from public.daily_orders" in s
            and "where user_id = %s" in s
            and "and order_date = %s" in s
            and "between" not in s
            and "update public.daily_orders" not in s
            and "insert into" not in s
            and "delete from" not in s
        ):
            user_id, order_date = params[0], params[1]
            base = ORDERS.get((user_id, order_date))
            self._rows = [_make_row(user_id, order_date, base)] if base else []
            return

        # LIST：where user_id = %s and order_date between %s and %s order by order_date
        if (
            "from public.daily_orders" in s
            and "where user_id = %s" in s
            and "between" in s
            and "update public.daily_orders" not in s
            and "insert into" not in s
            and "delete from" not in s
        ):
            user_id, from_d, to_d = params[0], params[1], params[2]
            out: list[dict[str, Any]] = []
            for (uid, dt), base in sorted(ORDERS.items()):
                if uid != user_id:
                    continue
                if from_d <= dt <= to_d:
                    out.append(_make_row(uid, dt, base))
            self._rows = out
            return

        # markPlayed 的 UPDATE；returning order_date
        if "update public.daily_orders" in s and "where user_id = %s" in s:
            # params = (played_at, played_at, user_id, order_date)
            played_at, _played_at2, user_id, order_date = (
                params[0],
                params[1],
                params[2],
                params[3],
            )
            key = (user_id, order_date)
            if key in ORDERS:
                ORDERS[key]["status"] = "played"
                ORDERS[key]["played_at"] = played_at
                self._rows = [{"order_date": order_date}]
                self._rowcount = 1
            else:
                self._rows = []
                self._rowcount = 0
            return

        # INSERT (save upsert)
        if "insert into public.daily_orders" in s:
            (
                user_id,
                order_date,
                topics_json,
                specific_request,
                status,
                delivery_time,
                played_at,
                entry_mode,
                length_tier,
            ) = params[:9]
            prior = ORDERS.get((user_id, order_date), {})
            topics = json.loads(topics_json) if isinstance(topics_json, str) else topics_json
            ORDERS[(user_id, order_date)] = {
                "selected_topics": topics,
                "specific_request": specific_request,
                "status": status,
                "delivery_time": delivery_time,
                "created_at": prior.get("created_at", "2026-07-17T00:00:00Z"),
                "updated_at": "2026-07-17T00:00:00Z",
                "played_at": played_at,
                "entry_mode": entry_mode,
                "length_tier": length_tier,
            }
            self._rows = []
            self._rowcount = 0
            return

        # DELETE
        if "delete from public.daily_orders" in s:
            user_id, order_date = params[0], params[1]
            existed = ORDERS.pop((user_id, order_date), None) is not None
            self._rowcount = 1 if existed else 0
            self._rows = []
            return

        self._rows = []
        self._rowcount = 0
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


async def fake_find_delivered_episode(
    user_id: str, deliver_date: str
) -> Episode | None:
    """簡化：USER_A 在 2026-07-15 有交付，其他都 null。"""
    if user_id == USER_A and deliver_date == "2026-07-15":
        return Episode(
            id="ep-a-only",
            title="T-A",
            title_zh=None,
            topic="tech",
            cefr_level="B1",
            is_free=False,
        )
    return None


@pytest.fixture(autouse=True)
def _reset_fixtures() -> None:
    _reset_state()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_orders_router, "connection", fake_connection)
    # /daily-orders/{date}/episode 走 repo.find_delivered_episode，直接 patch
    monkeypatch.setattr(db_repo, "find_delivered_episode", fake_find_delivered_episode)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _token(user_id: str) -> str:
    from tests._auth import sign_test_token

    return sign_test_token(user_id)


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


# ── (a) 無 JWT → 401（六 endpoint）───────────────────────────


def test_get_daily_order_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/daily-orders/2026-07-15")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_list_daily_orders_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/daily-orders?from_date=2026-07-01&to_date=2026-07-31")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_save_daily_order_no_jwt_returns_401(client: TestClient) -> None:
    res = client.put("/daily-orders", json={"date": "2026-07-17"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_mark_played_no_jwt_returns_401(client: TestClient) -> None:
    res = client.post(
        "/daily-orders/2026-07-16/played", json={"playedAt": "2026-07-16T08:00:00Z"}
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_delete_daily_order_no_jwt_returns_401(client: TestClient) -> None:
    res = client.delete("/daily-orders/2026-07-16")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_get_episode_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/daily-orders/2026-07-15/episode")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


# ── (b) happy path ──────────────────────────────────────────────


def test_get_daily_order_returns_saved_order(client: TestClient) -> None:
    res = client.get("/daily-orders/2026-07-15", headers=_auth(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data is not None
    assert data["date"] == "2026-07-15"
    assert data["selectedTopics"] == ["tech"]
    assert data["status"] == "played"
    assert data["entryMode"] == "topic"


def test_get_daily_order_returns_null_when_no_row(client: TestClient) -> None:
    res = client.get("/daily-orders/2099-01-01", headers=_auth(USER_A))
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_save_daily_order_upserts_and_returns(client: TestClient) -> None:
    res = client.put(
        "/daily-orders",
        json={
            "date": "2026-07-20",
            "selectedTopics": ["skill"],
            "specificRequest": "learn CORS",
            "status": "pending",
            "deliveryTime": "07:00",
            "entryMode": "knowledge",
            "lengthTier": "medium",
        },
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["date"] == "2026-07-20"
    assert data["selectedTopics"] == ["skill"]
    assert data["specificRequest"] == "learn CORS"
    assert (USER_A, "2026-07-20") in ORDERS


def test_list_daily_orders_filters_by_range(client: TestClient) -> None:
    res = client.get(
        "/daily-orders?from_date=2026-07-15&to_date=2026-07-16",
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    dates = [row["date"] for row in res.json()["data"]]
    assert dates == ["2026-07-15", "2026-07-16"]  # order by order_date


def test_mark_played_updates_status(client: TestClient) -> None:
    played_at = "2026-07-16T08:30:00Z"
    res = client.post(
        "/daily-orders/2026-07-16/played",
        json={"playedAt": played_at},
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "played"
    assert data["playedAt"] == played_at


def test_mark_played_returns_null_when_no_row(client: TestClient) -> None:
    # 找不到的日期 → 對齊 mockApi：data = null，不是 404
    res = client.post(
        "/daily-orders/2099-01-01/played",
        json={"playedAt": "2026-07-16T08:30:00Z"},
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_delete_daily_order_removes_row(client: TestClient) -> None:
    res = client.delete("/daily-orders/2026-07-16", headers=_auth(USER_A))
    assert res.status_code == 200
    assert (USER_A, "2026-07-16") not in ORDERS


def test_get_daily_order_episode_returns_delivered(client: TestClient) -> None:
    res = client.get("/daily-orders/2026-07-15/episode", headers=_auth(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data is not None
    assert data["id"] == "ep-a-only"


# ── (c) 授權收斂：A 拿不到 B 的訂單 ────────────────────────────


def test_get_daily_order_scoped_to_owner(client: TestClient) -> None:
    # A、B 在 2026-07-15 各自有訂單；token 不同 → selectedTopics 不一樣
    res_a = client.get("/daily-orders/2026-07-15", headers=_auth(USER_A))
    res_b = client.get("/daily-orders/2026-07-15", headers=_auth(USER_B))
    assert res_a.json()["data"]["selectedTopics"] == ["tech"]
    assert res_b.json()["data"]["selectedTopics"] == ["food"]


def test_get_daily_order_returns_null_when_other_user_has_it(
    client: TestClient,
) -> None:
    # A 查 B 沒訂單的日期 → null（owner scope，不是 200 + 別人的資料）
    # B 沒 2026-07-16 的訂單；以 A 視角查 2026-07-16 → 自己的，看到資料
    res_b_16 = client.get("/daily-orders/2026-07-16", headers=_auth(USER_B))
    assert res_b_16.json()["data"] is None


def test_list_daily_orders_scoped_to_owner(client: TestClient) -> None:
    res_a = client.get(
        "/daily-orders?from_date=2026-07-14&to_date=2026-07-17",
        headers=_auth(USER_A),
    )
    res_b = client.get(
        "/daily-orders?from_date=2026-07-14&to_date=2026-07-17",
        headers=_auth(USER_B),
    )
    dates_a = [r["date"] for r in res_a.json()["data"]]
    dates_b = [r["date"] for r in res_b.json()["data"]]
    assert dates_a == ["2026-07-15", "2026-07-16"]
    assert dates_b == ["2026-07-15"]


def test_delete_daily_order_scoped_to_owner(client: TestClient) -> None:
    # A 嘗試刪 B 在 2026-07-15 的訂單 → B 不可被誤刪
    client.delete("/daily-orders/2026-07-15", headers=_auth(USER_A))
    assert (USER_B, "2026-07-15") in ORDERS
    res_b = client.get("/daily-orders/2026-07-15", headers=_auth(USER_B))
    assert res_b.json()["data"]["selectedTopics"] == ["food"]


def test_get_daily_order_episode_scoped_to_owner(client: TestClient) -> None:
    # USER_A 在 2026-07-15 有交付；USER_B 同日 → null
    res_a = client.get("/daily-orders/2026-07-15/episode", headers=_auth(USER_A))
    res_b = client.get("/daily-orders/2026-07-15/episode", headers=_auth(USER_B))
    assert res_a.json()["data"] is not None
    assert res_b.json()["data"] is None
