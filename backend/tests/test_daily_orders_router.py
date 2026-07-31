"""Daily orders router 測試：隨時點餐、佇列制（migration 0024）。

驗證重點：
  (a) 無 JWT → 401（七個 endpoint 全驗，含新增的 active/history）
  (b) happy path：
      - create 建單 → 201 帶新 id；已有進行中訂單（pending/queued）→ 409
      - GET /active：有進行中訂單回該筆，沒有回 null
      - GET /history：只列 status='played'，cursor（limit/before）分頁
      - GET /{id}、markPlayed 改 status='played'、getEpisode 回 / null
      - DELETE /{id}：pending 成功；queued → 409（已開始生成不可取消）
  (c) 授權收斂：所有查詢/刪除都限定 owner；A 用自己的 token 查 B 的 order id
      拿不到資料（不是看到 B 的內容，是完全查無）

做法：照 test_api.py FakeConnection pattern，模擬 daily_orders table（改成以
id 為 key 的 dict，貼近真實 schema）；/daily-orders/{id}/episode 走
repo.find_delivered_episode，直接 patch 該函式。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from psycopg.errors import UniqueViolation

from app.routers import daily_orders as daily_orders_router
from shared.db import repo as db_repo
from tests._auth import auth_header
from tests._db_fakes import FakeConnection as _BaseFakeConnection
from tests._db_fakes import FakeCursor as _BaseFakeCursor
from tests._db_fakes import fake_connection

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

ORDER_A_PLAYED = "aaaaaaaa-0000-0000-0000-000000000001"
ORDER_A_PENDING = "aaaaaaaa-0000-0000-0000-000000000002"
ORDER_B_QUEUED = "bbbbbbbb-0000-0000-0000-000000000001"

# order_id → daily_orders 內部列（snake_case 對齊 DB，多帶 user_id 因為現在
# id 是 PK、user 收斂改用 where 子句而非字典的 key 結構）。
ORDERS: dict[str, dict[str, Any]] = {}


def _seed() -> dict[str, dict[str, Any]]:
    return {
        ORDER_A_PLAYED: {
            "user_id": USER_A,
            "order_date": "2026-07-15",
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
        ORDER_A_PENDING: {
            "user_id": USER_A,
            "order_date": "2026-07-16",
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
        ORDER_B_QUEUED: {
            "user_id": USER_B,
            "order_date": "2026-07-15",
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


def _make_row(order_id: str, base: dict[str, Any]) -> dict[str, Any]:
    """對齊 router 的 _SELECT 投影（to_char 攤平成 date / ISO 字串欄位）。"""
    return {
        "id": order_id,
        "date": base["order_date"],
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


_next_seq = 0


def _reset_state() -> None:
    global _next_seq
    ORDERS.clear()
    ORDERS.update(_seed())
    _next_seq = 0


class FakeCursor(_BaseFakeCursor):
    def __init__(self) -> None:
        super().__init__()
        self._rowcount: int = 0

    @property
    def rowcount(self) -> int:
        return self._rowcount

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        global _next_seq
        s = " ".join(sql.split())
        self._rows = []
        self._rowcount = 0

        # CREATE：insert ... returning id。撞「已有進行中訂單」→ UniqueViolation，
        # 鏡射 migration 0024 的 partial unique index。
        if "insert into public.daily_orders" in s:
            (
                user_id,
                order_date,
                topics_json,
                specific_request,
                delivery_time,
                entry_mode,
                length_tier,
            ) = params[:7]
            has_active = any(
                row["user_id"] == user_id and row["status"] in ("pending", "queued")
                for row in ORDERS.values()
            )
            if has_active:
                raise UniqueViolation("simulated: idx_daily_orders_one_active_per_user")
            _next_seq += 1
            new_id = f"created-{_next_seq:04d}"
            topics = json.loads(topics_json) if isinstance(topics_json, str) else topics_json
            ORDERS[new_id] = {
                "user_id": user_id,
                "order_date": order_date,
                "selected_topics": topics,
                "specific_request": specific_request,
                "status": "pending",
                "delivery_time": delivery_time,
                "created_at": f"{order_date}T00:00:00Z",
                "updated_at": f"{order_date}T00:00:00Z",
                "played_at": None,
                "entry_mode": entry_mode,
                "length_tier": length_tier,
            }
            self._rows = [{"id": new_id}]
            return

        # markPlayed：UPDATE ... SET status='played' ... WHERE user_id=%s AND id=%s
        if "update public.daily_orders" in s and "status = 'played'" in s:
            played_at, _played_at2, user_id, order_id = params
            row = ORDERS.get(order_id)
            if row is not None and row["user_id"] == user_id:
                row["status"] = "played"
                row["played_at"] = played_at
                self._rows = [{"id": order_id}]
                self._rowcount = 1
            return

        # DELETE：只有 pending 才刪得掉（atomic WHERE status='pending' RETURNING）
        if "delete from public.daily_orders" in s and "returning id" in s:
            user_id, order_id = params
            row = ORDERS.get(order_id)
            if row is not None and row["user_id"] == user_id and row["status"] == "pending":
                del ORDERS[order_id]
                self._rows = [{"id": order_id}]
                self._rowcount = 1
            return

        # DELETE 失敗後的現狀查詢：分辨 404（查無）vs 409（非 pending）
        if "select status from public.daily_orders where user_id = %s and id = %s" in s:
            user_id, order_id = params
            row = ORDERS.get(order_id)
            if row is not None and row["user_id"] == user_id:
                self._rows = [{"status": row["status"]}]
            return

        # GET /active：where user_id = %s and status in ('pending', 'queued')
        if "from public.daily_orders" in s and "status in ('pending', 'queued')" in s:
            (user_id,) = params
            matches = [
                (oid, row)
                for oid, row in ORDERS.items()
                if row["user_id"] == user_id and row["status"] in ("pending", "queued")
            ]
            matches.sort(key=lambda kv: kv[1]["created_at"], reverse=True)
            self._rows = [_make_row(oid, row) for oid, row in matches[:1]]
            return

        # GET /history：where user_id = %s and status in ('ready','played','expired')
        # [and created_at<%s]。migration 0027 後 expired 也算歷史
        # （reconcile 退役的卡死訂單，使用者看得到）
        if "where user_id = %s and status in ('ready', 'played', 'expired')" in s:
            user_id = params[0]
            before = params[1] if "created_at < %s" in s else None
            limit = params[-1]
            matches = [
                (oid, row)
                for oid, row in ORDERS.items()
                if row["user_id"] == user_id
                and row["status"] in ("ready", "played", "expired")
                and (before is None or row["created_at"] < before)
            ]
            matches.sort(key=lambda kv: kv[1]["created_at"], reverse=True)
            self._rows = [_make_row(oid, row) for oid, row in matches[:limit]]
            return

        # GET /{id}：where user_id = %s and id = %s
        if (
            "select id::text as id" in s
            and "from public.daily_orders" in s
            and "where user_id = %s and id = %s" in s
        ):
            user_id, order_id = params
            row = ORDERS.get(order_id)
            if row is not None and row["user_id"] == user_id:
                self._rows = [_make_row(order_id, row)]
            return

        # 建立後緊接的「where id = %s」重查（沒有 user_id 條件，router 內部信任
        # 剛拿到的 id）
        if "select id::text as id" in s and "where id = %s" in s:
            (order_id,) = params
            row = ORDERS.get(order_id)
            if row is not None:
                self._rows = [_make_row(order_id, row)]
            return

        return


class FakeConnection(_BaseFakeConnection):
    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()


async def fake_find_delivered_episode(user_id: str, order_id: str) -> dict[str, Any] | None:
    """簡化：USER_A 的 ORDER_A_PLAYED 有交付，其他都 null。

    回傳原始 row（不是 Episode）：router 層改呼叫 services/episode_assembly.py
    的 build_episode() 組裝，跟 repo.find_delivered_episode() 的真實回傳型別一致。
    """
    if user_id == USER_A and order_id == ORDER_A_PLAYED:
        return {
            "slug": "ep-a-only",
            "title": "T-A",
            "title_zh": None,
            "topic": "tech",
            "cefr_level": "B1",
            "is_free": False,
            "script_json": None,
            "sources": None,
            "audio_r2_key": None,
            "audio_r2_keys": None,
        }
    return None


@pytest.fixture(autouse=True)
def _reset_fixtures() -> None:
    _reset_state()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_orders_router, "connection", fake_connection(FakeConnection))
    monkeypatch.setattr(db_repo, "find_delivered_episode", fake_find_delivered_episode)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


# ── (a) 無 JWT → 401 ─────────────────────────────────────────────


def test_get_daily_order_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get(f"/daily-orders/{ORDER_A_PLAYED}")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_get_active_order_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/daily-orders/active")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_list_order_history_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/daily-orders/history")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_create_daily_order_no_jwt_returns_401(client: TestClient) -> None:
    res = client.post("/daily-orders", json={"selectedTopics": ["tech"]})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_mark_played_no_jwt_returns_401(client: TestClient) -> None:
    res = client.post(
        f"/daily-orders/{ORDER_A_PENDING}/played",
        json={"playedAt": "2026-07-16T08:00:00Z"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_delete_daily_order_no_jwt_returns_401(client: TestClient) -> None:
    res = client.delete(f"/daily-orders/{ORDER_A_PENDING}")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_get_episode_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get(f"/daily-orders/{ORDER_A_PLAYED}/episode")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


# ── (b) happy path ──────────────────────────────────────────────


def test_get_daily_order_returns_saved_order(client: TestClient) -> None:
    res = client.get(f"/daily-orders/{ORDER_A_PLAYED}", headers=auth_header(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data is not None
    assert data["id"] == ORDER_A_PLAYED
    assert data["date"] == "2026-07-15"
    assert data["selectedTopics"] == ["tech"]
    assert data["status"] == "played"
    assert data["entryMode"] == "topic"


def test_get_daily_order_returns_null_when_no_row(client: TestClient) -> None:
    res = client.get("/daily-orders/does-not-exist", headers=auth_header(USER_A))
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_create_daily_order_returns_201_with_new_id(client: TestClient) -> None:
    ORDERS.clear()  # USER_A 沒有任何進行中訂單
    res = client.post(
        "/daily-orders",
        json={
            "selectedTopics": ["skill"],
            "specificRequest": "learn CORS",
            "deliveryTime": "07:00",
            "entryMode": "knowledge",
            "lengthTier": "medium",
        },
        headers=auth_header(USER_A),
    )
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["id"]
    assert data["status"] == "pending"
    assert data["selectedTopics"] == ["skill"]
    assert data["specificRequest"] == "learn CORS"
    assert data["id"] in ORDERS


def test_create_daily_order_conflict_when_active_order_exists(client: TestClient) -> None:
    # USER_A 已有 ORDER_A_PENDING（status='pending'）在種子資料中
    res = client.post(
        "/daily-orders",
        json={"selectedTopics": ["skill"]},
        headers=auth_header(USER_A),
    )
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "conflict"


def test_get_active_order_returns_in_flight_order(client: TestClient) -> None:
    res = client.get("/daily-orders/active", headers=auth_header(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data is not None
    assert data["id"] == ORDER_A_PENDING
    assert data["status"] == "pending"


def test_get_active_order_returns_null_when_none(client: TestClient) -> None:
    # USER_B 只有 queued 訂單，但把它先播放完，讓 B 沒有任何進行中訂單
    ORDERS[ORDER_B_QUEUED]["status"] = "played"
    res = client.get("/daily-orders/active", headers=auth_header(USER_B))
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_history_paginated_by_created_at_desc(client: TestClient) -> None:
    ORDERS.clear()
    ORDERS.update(
        {
            "h1": {
                "user_id": USER_A,
                "order_date": "2026-07-01",
                "selected_topics": [],
                "specific_request": None,
                "status": "played",
                "delivery_time": "07:00",
                "created_at": "2026-07-01T00:00:00Z",
                "updated_at": "2026-07-01T00:00:00Z",
                "played_at": "2026-07-01T01:00:00Z",
                "entry_mode": "topic",
                "length_tier": "medium",
            },
            "h2": {
                "user_id": USER_A,
                "order_date": "2026-07-02",
                "selected_topics": [],
                "specific_request": None,
                "status": "played",
                "delivery_time": "07:00",
                "created_at": "2026-07-02T00:00:00Z",
                "updated_at": "2026-07-02T00:00:00Z",
                "played_at": "2026-07-02T01:00:00Z",
                "entry_mode": "topic",
                "length_tier": "medium",
            },
            "h3-pending": {
                "user_id": USER_A,
                "order_date": "2026-07-03",
                "selected_topics": [],
                "specific_request": None,
                "status": "pending",  # 進行中的不該出現在 history
                "delivery_time": "07:00",
                "created_at": "2026-07-03T00:00:00Z",
                "updated_at": "2026-07-03T00:00:00Z",
                "played_at": None,
                "entry_mode": "topic",
                "length_tier": "medium",
            },
        }
    )

    res = client.get("/daily-orders/history?limit=1", headers=auth_header(USER_A))
    assert res.status_code == 200
    page1 = res.json()["data"]
    assert [o["id"] for o in page1] == ["h2"]  # 最新的先來，pending 不出現

    res2 = client.get(
        f"/daily-orders/history?limit=1&before={page1[0]['createdAt']}",
        headers=auth_header(USER_A),
    )
    page2 = res2.json()["data"]
    assert [o["id"] for o in page2] == ["h1"]


def test_mark_played_updates_status(client: TestClient) -> None:
    played_at = "2026-07-16T08:30:00Z"
    res = client.post(
        f"/daily-orders/{ORDER_A_PENDING}/played",
        json={"playedAt": played_at},
        headers=auth_header(USER_A),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["status"] == "played"
    assert data["playedAt"] == played_at


def test_mark_played_returns_null_when_no_row(client: TestClient) -> None:
    res = client.post(
        "/daily-orders/does-not-exist/played",
        json={"playedAt": "2026-07-16T08:30:00Z"},
        headers=auth_header(USER_A),
    )
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_delete_pending_order_succeeds(client: TestClient) -> None:
    res = client.delete(f"/daily-orders/{ORDER_A_PENDING}", headers=auth_header(USER_A))
    assert res.status_code == 200
    assert ORDER_A_PENDING not in ORDERS


def test_delete_queued_order_returns_409(client: TestClient) -> None:
    res = client.delete(f"/daily-orders/{ORDER_B_QUEUED}", headers=auth_header(USER_B))
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "conflict"
    assert ORDER_B_QUEUED in ORDERS  # 沒被刪掉


def test_delete_missing_order_returns_404(client: TestClient) -> None:
    res = client.delete("/daily-orders/does-not-exist", headers=auth_header(USER_A))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_get_daily_order_episode_returns_delivered(client: TestClient) -> None:
    res = client.get(f"/daily-orders/{ORDER_A_PLAYED}/episode", headers=auth_header(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data is not None
    assert data["id"] == "ep-a-only"


# ── (c) 授權收斂：A 拿不到 B 的訂單 ────────────────────────────


def test_get_daily_order_scoped_to_owner(client: TestClient) -> None:
    # B 用自己的 token 查 A 的 order id → 查無（不是看到 A 的內容）
    res = client.get(f"/daily-orders/{ORDER_A_PLAYED}", headers=auth_header(USER_B))
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_get_active_order_scoped_to_owner(client: TestClient) -> None:
    res_a = client.get("/daily-orders/active", headers=auth_header(USER_A))
    res_b = client.get("/daily-orders/active", headers=auth_header(USER_B))
    assert res_a.json()["data"]["id"] == ORDER_A_PENDING
    assert res_b.json()["data"]["id"] == ORDER_B_QUEUED


def test_delete_daily_order_scoped_to_owner(client: TestClient) -> None:
    # B 嘗試刪 A 的 pending 訂單 → 查無（404），A 的資料不受影響
    res = client.delete(f"/daily-orders/{ORDER_A_PENDING}", headers=auth_header(USER_B))
    assert res.status_code == 404
    assert ORDER_A_PENDING in ORDERS


def test_get_daily_order_episode_scoped_to_owner(client: TestClient) -> None:
    res_a = client.get(f"/daily-orders/{ORDER_A_PLAYED}/episode", headers=auth_header(USER_A))
    res_b = client.get(f"/daily-orders/{ORDER_A_PLAYED}/episode", headers=auth_header(USER_B))
    assert res_a.json()["data"] is not None
    assert res_b.json()["data"] is None
