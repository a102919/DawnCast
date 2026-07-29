"""使用者端公開頻道 router 測試（app/routers/channels.py）。

驗證重點：
  (a) 無 JWT → 401（subscribe/unsubscribe/subscriptions 三個需登入的 endpoint）
  (b) GET /channels 只回 status=active；GET /channels/{slug} 查無回 404
  (c) subscribe/unsubscribe 冪等（連打兩次不炸）
  (d) 授權收斂：GET /channels/subscriptions 只回自己追蹤的頻道，看不到別人的

做法：比照 tests/test_favorites_router.py 的 FakeCursor pattern，用 in-memory dict
模擬 channels / user_channel_subscriptions 兩張表的關聯行為。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import channels as channels_router
from tests._auth import auth_header
from tests._db_fakes import FakeConnection as _BaseFakeConnection
from tests._db_fakes import FakeCursor as _BaseFakeCursor
from tests._db_fakes import fake_connection

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

# slug → 頻道列（模擬 public.channels；status 決定探索頁看不看得到）
CHANNELS: dict[str, dict[str, Any]] = {
    "tech-daily": {
        "id": "chan-tech",
        "slug": "tech-daily",
        "name": "科技日報",
        "description": "每日科技新聞",
        "topic": "tech",
        "cover_r2_key": None,
        "episode_count": 5,
        "status": "active",
    },
    "paused-show": {
        "id": "chan-paused",
        "slug": "paused-show",
        "name": "已暫停節目",
        "description": None,
        "topic": "culture",
        "cover_r2_key": None,
        "episode_count": 1,
        "status": "paused",
    },
}

# (user_id, channel_id) 現有訂閱
SUBSCRIPTIONS: list[tuple[str, str]] = [("11111111-1111-1111-1111-111111111111", "chan-tech")]


def _reset_state() -> None:
    SUBSCRIPTIONS.clear()
    SUBSCRIPTIONS.append((USER_A, "chan-tech"))


def _channel_by_id(channel_id: str) -> dict[str, Any] | None:
    for row in CHANNELS.values():
        if row["id"] == channel_id:
            return row
    return None


class FakeCursor(_BaseFakeCursor):
    async def execute(self, sql: str, params: Any = ()) -> None:
        s = " ".join(sql.split())
        self._rows = []

        # GET /channels：status='active' 過濾
        if "from public.channels c" in s and "where (%(status)s" in s:
            status = params["status"]
            self._rows = [
                dict(row) for row in CHANNELS.values() if status is None or row["status"] == status
            ]
            return

        # GET /channels/{slug}、subscribe/unsubscribe 用來 resolve slug → row
        if "from public.channels c where c.slug = %s" in s:
            slug = params[0]
            row = CHANNELS.get(slug)
            self._rows = [dict(row)] if row else []
            return

        # POST /channels/{slug}/subscribe
        if "insert into public.user_channel_subscriptions" in s:
            user_id, channel_id = params[0], params[1]
            if not any(u == user_id and c == channel_id for u, c in SUBSCRIPTIONS):
                SUBSCRIPTIONS.append((user_id, channel_id))
            self._rows = []
            return

        # DELETE /channels/{slug}/subscribe
        if "delete from public.user_channel_subscriptions" in s:
            user_id, channel_id = params[0], params[1]
            SUBSCRIPTIONS[:] = [
                (u, c) for u, c in SUBSCRIPTIONS if not (u == user_id and c == channel_id)
            ]
            self._rows = []
            return

        # GET /channels/subscriptions：join，不篩 status（見 list_subscribed_channels docstring）
        if "join public.user_channel_subscriptions s on s.channel_id = c.id" in s:
            user_id = params[0]
            out: list[dict[str, Any]] = []
            for u, channel_id in SUBSCRIPTIONS:
                if u != user_id:
                    continue
                row = _channel_by_id(channel_id)
                if row:
                    out.append(dict(row))
            self._rows = out
            return

        return


class FakeConnection(_BaseFakeConnection):
    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()


@pytest.fixture(autouse=True)
def _reset_fixtures() -> None:
    _reset_state()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = fake_connection(FakeConnection)
    monkeypatch.setattr(channels_router.channels_db, "connection", conn)
    # cover 一律留空，不打真 R2（見 CHANNELS 全部 cover_r2_key=None，理論上用不到，
    # 但保險起見比照其他 router 測試 patch 掉）。
    monkeypatch.setattr(
        channels_router.r2,
        "presigned_get_url",
        lambda key, ttl=None: f"https://signed.example/{key}",
    )
    monkeypatch.setattr(
        channels_router.r2,
        "presigned_get_urls",
        lambda keys, ttl=None: {k: f"https://signed.example/{k}" for k in keys},
    )


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


# ── (a) 無 JWT → 401 ──────────────────────────────────────────────


def test_subscribe_no_jwt_returns_401(client: TestClient) -> None:
    res = client.post("/channels/tech-daily/subscribe")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_unsubscribe_no_jwt_returns_401(client: TestClient) -> None:
    res = client.delete("/channels/tech-daily/subscribe")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_list_subscriptions_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/channels/subscriptions")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


# ── (b) GET /channels 只回 active；查無回 404 ──────────────────────


def test_list_channels_only_returns_active(client: TestClient) -> None:
    res = client.get("/channels")
    assert res.status_code == 200
    slugs = {c["slug"] for c in res.json()["data"]}
    assert slugs == {"tech-daily"}  # paused-show 被濾掉


def test_get_channel_not_found_returns_404(client: TestClient) -> None:
    res = client.get("/channels/no-such-channel")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_get_channel_found_returns_public_shape(client: TestClient) -> None:
    res = client.get("/channels/tech-daily")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["slug"] == "tech-daily"
    assert data["name"] == "科技日報"
    # ChannelPublic 刻意不含 themePrompt：不該外洩內部選題指令
    assert "themePrompt" not in data


# ── (c) subscribe / unsubscribe 冪等 ───────────────────────────────


def test_subscribe_twice_is_idempotent(client: TestClient) -> None:
    res1 = client.post("/channels/paused-show/subscribe", headers=auth_header(USER_B))
    res2 = client.post("/channels/paused-show/subscribe", headers=auth_header(USER_B))
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert SUBSCRIPTIONS.count((USER_B, "chan-paused")) == 1


def test_unsubscribe_when_not_subscribed_is_idempotent(client: TestClient) -> None:
    res = client.delete("/channels/tech-daily/subscribe", headers=auth_header(USER_B))
    assert res.status_code == 200


def test_subscribe_unknown_slug_returns_404(client: TestClient) -> None:
    res = client.post("/channels/no-such-channel/subscribe", headers=auth_header(USER_A))
    assert res.status_code == 404


# ── (d) 授權收斂：GET /channels/subscriptions 只回自己的 ──────────────


def test_list_my_subscriptions_scoped_to_owner(client: TestClient) -> None:
    res_a = client.get("/channels/subscriptions", headers=auth_header(USER_A))
    res_b = client.get("/channels/subscriptions", headers=auth_header(USER_B))
    slugs_a = {c["slug"] for c in res_a.json()["data"]}
    slugs_b = {c["slug"] for c in res_b.json()["data"]}
    assert slugs_a == {"tech-daily"}
    assert slugs_b == set()  # B 預設沒有任何訂閱


def test_list_my_subscriptions_includes_paused_channel(client: TestClient) -> None:
    """使用者追蹤的頻道被 admin 暫停後不該從清單消失（見 repo docstring）。"""
    client.post("/channels/paused-show/subscribe", headers=auth_header(USER_A))
    res = client.get("/channels/subscriptions", headers=auth_header(USER_A))
    slugs = {c["slug"] for c in res.json()["data"]}
    assert slugs == {"tech-daily", "paused-show"}
