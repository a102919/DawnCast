"""Admin / ops endpoint 測試（T7）。

授權機制與一般 API 完全不同（X-Admin-Token 固定字串比對，非 Supabase JWT），
故自成一份測試檔、自帶 FakeConnection，不共用 test_api.py 的 patch_db fixture。

驗證重點：
  (a) 帶正確 X-Admin-Token → 200，資料形狀正確（camelCase）
  (b) 不帶 / 帶錯 token → 401
  (c) 帶合法 Supabase JWT 但不帶 admin token → 仍 401（兩套授權互不相通）
  (d) ADMIN_TOKEN 未設定（空字串）時 fail-closed，一律拒絕
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.routers import admin as admin_router
from shared.config import Settings, get_settings

ADMIN_TOKEN = "test-admin-token"

_EPISODE_ROWS: list[dict[str, Any]] = [
    {
        "id": "ep-2",
        "title": "Episode 2",
        "topic": "tech",
        "cefr_level": "B1",
        "is_free": False,
        "is_featured": True,
        "episode_no": 2,
        "published_at": "2026-07-16",
        "created_at": "2026-07-16T00:00:00Z",
        "freshness_class": "timely",
        "expires_at": None,
        "has_audio": True,
    },
    {
        "id": "ep-1",
        "title": "Episode 1",
        "topic": "news",
        "cefr_level": "A2",
        "is_free": True,
        "is_featured": False,
        "episode_no": 1,
        "published_at": "2026-07-15",
        "created_at": "2026-07-15T00:00:00Z",
        "freshness_class": "evergreen",
        "expires_at": None,
        "has_audio": False,
    },
]

_JOB_ROWS: list[dict[str, Any]] = [
    {
        "queue_name": "control",
        "queue_length": 0,
        "newest_msg_age_sec": None,
        "oldest_msg_age_sec": None,
        "total_messages": 0,
    },
    {
        "queue_name": "generate",
        "queue_length": 3,
        "newest_msg_age_sec": 5,
        "oldest_msg_age_sec": 120,
        "total_messages": 10,
    },
]

_TOKEN_ITEM_ROWS: list[dict[str, Any]] = [
    {
        "slug": "ep-2",
        "title": "Episode 2",
        "input_tokens": 500,
        "output_tokens": 300,
        "created_at": "2026-07-16T00:00:00Z",
    },
    {
        "slug": "ep-1",
        "title": "Episode 1",
        "input_tokens": 200,
        "output_tokens": 100,
        "created_at": "2026-07-15T00:00:00Z",
    },
]


class FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())  # 正規化空白

        if "has_audio" in s and "from public.episodes" in s:
            self._rows = list(_EPISODE_ROWS)
            return

        if "pgmq.metrics_all" in s:
            self._rows = list(_JOB_ROWS)
            return

        if "coalesce(sum(input_tokens)" in s:
            total_input = sum(r["input_tokens"] for r in _TOKEN_ITEM_ROWS)
            total_output = sum(r["output_tokens"] for r in _TOKEN_ITEM_ROWS)
            self._rows = [
                {
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "episode_count": len(_TOKEN_ITEM_ROWS),
                }
            ]
            return

        if "input_tokens, output_tokens" in s and "from public.episodes" in s:
            self._rows = list(_TOKEN_ITEM_ROWS)
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
def patch_admin_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin_router, "connection", fake_connection)
    # 獨立於全域 get_settings() 的 lru_cache 單例，避免污染其他測試檔。
    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_token=ADMIN_TOKEN),
    )


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _admin_headers(token: str) -> dict[str, str]:
    return {"X-Admin-Token": token}


def _jwt_headers() -> dict[str, str]:
    settings = get_settings()
    token = jwt.encode(
        {"sub": "some-user-id", "aud": settings.supabase_jwt_audience},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# ── /admin/episodes ────────────────────────────────────────────────


def test_episodes_no_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/episodes")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_episodes_wrong_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/episodes", headers=_admin_headers("wrong-token"))
    assert res.status_code == 401
    assert res.json()["ok"] is False


def test_episodes_correct_token_returns_200(client: TestClient) -> None:
    res = client.get("/admin/episodes", headers=_admin_headers(ADMIN_TOKEN))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2
    item = body["data"][0]
    assert item["id"] == "ep-2"
    assert item["hasAudio"] is True
    assert "cefrLevel" in item
    assert "episodeNo" in item
    assert "freshnessClass" in item


# ── /admin/jobs ───────────────────────────────────────────────────


def test_jobs_no_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/jobs")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_jobs_correct_token_returns_200(client: TestClient) -> None:
    res = client.get("/admin/jobs", headers=_admin_headers(ADMIN_TOKEN))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert len(data) == 2
    by_name = {row["queueName"]: row for row in data}
    assert by_name["control"]["queueLength"] == 0
    assert by_name["control"]["newestMsgAgeSec"] is None  # 空佇列 NULL age 不炸掉
    assert by_name["generate"]["queueLength"] == 3
    assert by_name["generate"]["oldestMsgAgeSec"] == 120


# ── /admin/token-usage ────────────────────────────────────────────


def test_token_usage_no_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/token-usage")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_token_usage_correct_token_returns_200(client: TestClient) -> None:
    res = client.get("/admin/token-usage", headers=_admin_headers(ADMIN_TOKEN))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["totalInputTokens"] == 700
    assert data["totalOutputTokens"] == 400
    assert data["episodeCount"] == 2
    items = data["items"]
    assert [i["slug"] for i in items] == ["ep-2", "ep-1"]  # createdAt desc


# ── 兩套授權機制互不相通 / fail-closed ──────────────────────────────


def test_using_supabase_jwt_instead_of_admin_token_still_401(client: TestClient) -> None:
    res = client.get("/admin/episodes", headers=_jwt_headers())
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_admin_token_unset_denies_even_empty_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        admin_router, "get_settings", lambda: Settings(environment="dev", admin_token="")
    )
    res_no_header = client.get("/admin/episodes")
    assert res_no_header.status_code == 401

    res_empty_header = client.get("/admin/episodes", headers=_admin_headers(""))
    assert res_empty_header.status_code == 401
