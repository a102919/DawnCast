"""Favorites router 測試（T9）：list / add / remove。

驗證重點：
  (a) 無 JWT → 401（三 endpoint 全驗）
  (b) happy path：list 回 slug[]；對未知 slug → 404
  (c) 授權收斂：A 的 token 不能看到、加進、刪掉 B 的收藏
      （若 router 漏 where user_id = %s，A 會把收藏寫進 B 名下或看到 B 的）

做法：照 test_api.py FakeConnection pattern，攔截 user_favorites JOIN episodes 與
slug→uuid 的 lookup。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import favorites as favorites_router

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

# slug → uuid（模擬 episodes 表，_slug_to_uuid 用）
EPISODES: dict[str, str] = {
    "ep-a": "uuid-ep-a",
    "ep-b": "uuid-ep-b",
    "ep-shared": "uuid-ep-shared",
}

# (user_id, episode_uuid) 對應現有收藏
FAVORITES: list[tuple[str, str]] = [
    (USER_A, "uuid-ep-a"),
    (USER_A, "uuid-ep-shared"),
    (USER_B, "uuid-ep-b"),
]


def _reset_state() -> None:
    FAVORITES.clear()
    FAVORITES.extend(
        [
            (USER_A, "uuid-ep-a"),
            (USER_A, "uuid-ep-shared"),
            (USER_B, "uuid-ep-b"),
        ]
    )


def _slug_for(ep_uuid: str) -> str | None:
    for slug, uid in EPISODES.items():
        if uid == ep_uuid:
            return slug
    return None


class FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())

        # slug → uuid lookup（_slug_to_uuid）
        if "select id from public.episodes where slug = %s" in s:
            slug = params[0]
            uid = EPISODES.get(slug)
            self._rows = [{"id": uid}] if uid else []
            return

        # SELECT FROM user_favorites JOIN episodes（list 用此 JOIN）
        # 顯式要求 "where f.user_id = %s"，若 router 把 WHERE 拿掉 → branch 不匹配
        # → 回空，test 失敗（不靠 params[0] 推測 user_id，避免假綠）。
        if (
            "from public.user_favorites f" in s
            and "join public.episodes e" in s
            and "where f.user_id = %s" in s
        ):
            # list：params = (user_id,)
            user_id = params[0]
            out: list[dict[str, Any]] = []
            for uid, ep_uuid in FAVORITES:
                if uid != user_id:
                    continue
                slug = _slug_for(ep_uuid)
                if slug:
                    out.append({"slug": slug})
            self._rows = out
            return

        # INSERT INTO user_favorites
        if "insert into public.user_favorites" in s:
            user_id, ep_uuid = params[0], params[1]
            if not any(u == user_id and e == ep_uuid for u, e in FAVORITES):
                FAVORITES.append((user_id, ep_uuid))
            self._rows = []
            return

        # DELETE FROM user_favorites USING episodes
        if "delete from public.user_favorites" in s:
            slug, user_id = params[0], params[1]
            ep_uuid = EPISODES.get(slug)
            FAVORITES[:] = [
                (u, e) for u, e in FAVORITES if not (u == user_id and e == ep_uuid)
            ]
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
    monkeypatch.setattr(favorites_router, "connection", fake_connection)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _token(user_id: str) -> str:
    from tests._auth import sign_test_token

    return sign_test_token(user_id)


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


# ── (a) 無 JWT → 401（三 endpoint 全驗）─────────────────────────


def test_list_favorites_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/favorites")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_add_favorite_no_jwt_returns_401(client: TestClient) -> None:
    res = client.post("/favorites/ep-shared")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_remove_favorite_no_jwt_returns_401(client: TestClient) -> None:
    res = client.delete("/favorites/ep-a")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


# ── (b) happy path ────────────────────────────────────────────


def test_list_favorites_returns_user_slugs(client: TestClient) -> None:
    res = client.get("/favorites", headers=_auth(USER_A))
    assert res.status_code == 200
    slugs = res.json()["data"]
    # A 有 ep-a 與 ep-shared 兩個收藏
    assert set(slugs) == {"ep-a", "ep-shared"}


def test_add_favorite_unknown_slug_returns_404(client: TestClient) -> None:
    res = client.post("/favorites/no-such-ep", headers=_auth(USER_A))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    # 404 在 _slug_to_uuid 階段就 raise，沒走到 insert；驗 FAVORITES 集合未變動
    # （一個帶未知 uuid 的 tuple 不該出現在 FAVORITES，因為 FAVORITES 的 uuid 都來自 EPISODES）
    assert ("no-such-ep",) not in {(e,) for _, e in FAVORITES}


# ── (c) 授權收斂：A 看不到 B 的收藏 ───────────────────────────


def test_list_favorites_scoped_to_owner(client: TestClient) -> None:
    res_a = client.get("/favorites", headers=_auth(USER_A))
    res_b = client.get("/favorites", headers=_auth(USER_B))
    slugs_a = res_a.json()["data"]
    slugs_b = res_b.json()["data"]
    # 數量對齊 fixture，否則 fake 在 WHERE 拿掉時也會回空 list 讓 "x not in []"
    # 通過（vacuously true）。先卡數量，再驗沒有對方的資料。
    assert len(slugs_a) == 2
    assert len(slugs_b) == 1
    assert set(slugs_a) == {"ep-a", "ep-shared"}
    assert set(slugs_b) == {"ep-b"}
    # 額外嚴格：A 看到 B 的收藏就炸。
    assert "ep-b" not in slugs_a
    assert "ep-a" not in slugs_b
    assert "ep-shared" not in slugs_b


def test_remove_favorite_scoped_to_owner(client: TestClient) -> None:
    # A 嘗試刪 B 的 ep-b → 不可動到 B（B 仍收藏，用 list 驗證）
    client.delete("/favorites/ep-b", headers=_auth(USER_A))
    res_b = client.get("/favorites", headers=_auth(USER_B))
    assert "ep-b" in res_b.json()["data"]
