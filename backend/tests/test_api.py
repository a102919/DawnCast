"""API 服務層測試（FastAPI TestClient + 假 DB）。

驗證重點：
  (a) 無 JWT → 401
  (b) 授權收斂：A 的 token 只讀得到 A 的 vocab，讀不到 B 的
  (c) ApiResponse envelope 形狀正確（{ok, data, error}）
  (d) episodes/{slug}/url 對無權集回 403、有權/免費集回簽章 URL

做法：不連真 DB。用 FakeConnection 攔截參數化 SQL，依關鍵字 +
user_id 參數回傳預置的 in-memory 列。重點驗 router 邏輯與授權收斂，
而非 Postgres 行為（SQL 本身的正確性靠型別/migration 保證）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.routers import daily_orders as daily_orders_router
from app.routers import episodes as episodes_router
from app.routers import vocab as vocab_router
from shared.config import get_settings
from shared.db import repo as db_repo

# ── 測試資料：兩個 user，各自的 vocab；三集（免費 / A 有授權 / 都無授權）──

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

VOCAB_BY_USER: dict[str, list[dict[str, Any]]] = {
    USER_A: [
        {
            "id": "aaaa1111-0000-0000-0000-000000000001",
            "word": "serendipity",
            "lemma": "serendipity",
            "pos": "n",
            "translation": "意外發現",
            "ipa": None,
            "source_episode_id": "ep-free",
            "source_line_no": 3,
            "source_timestamp": 12.5,
            "created_at": "2026-06-01T00:00:00Z",
            "sense_idx": 0,
            "source_sentence": None,
            "next_review": "2026-06-02",
            "interval": 1,
            "ease": 2.5,
        }
    ],
    USER_B: [
        {
            "id": "bbbb2222-0000-0000-0000-000000000001",
            "word": "ephemeral",
            "lemma": "ephemeral",
            "pos": "adj",
            "translation": "短暫的",
            "ipa": None,
            "source_episode_id": "ep-free",
            "source_line_no": 5,
            "source_timestamp": 20.0,
            "created_at": "2026-06-01T00:00:00Z",
            "sense_idx": 0,
            "source_sentence": None,
            "next_review": "2026-06-02",
            "interval": 1,
            "ease": 2.5,
        }
    ],
}

# slug → (is_free, A 是否有 delivery, B 是否有 delivery, 有無媒體 key)
EPISODES: dict[str, dict[str, Any]] = {
    "ep-free": {"is_free": True, "deliveries": set(), "key": "media/ep-free.mp4"},
    "ep-a-only": {"is_free": False, "deliveries": {USER_A}, "key": "media/ep-a.mp4"},
    "ep-locked": {"is_free": False, "deliveries": set(), "key": "media/ep-locked.mp4"},
}

# (user_id, deliver_date) → 對應交付集數的 slug list。模擬 user 在指定日期的交付事實。
DELIVERIES_BY_DATE: dict[tuple[str, str], list[str]] = {
    (USER_A, "2026-07-15"): ["ep-a-only"],
}


class FakeCursor:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    async def __aenter__(self) -> FakeCursor:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())  # 正規化空白
        self._rows = []

        # vocab list / search：where v.user_id = %s（user_id 為第一個參數）
        if "from public.user_vocab v" in s and "where v.user_id = %s" in s:
            user_id = params[0]
            self._rows = list(VOCAB_BY_USER.get(user_id, []))
            return

        # episodes 授權查詢（_fetch_authorized）
        if "from public.episodes e where e.slug = %s" in s:
            user_id, slug = params[0], params[1]
            ep = EPISODES.get(slug)
            if ep is None:
                self._rows = []
                return
            self._rows = [
                {
                    "id": f"uuid-{slug}",
                    "slug": slug,
                    "title": "T",
                    "title_zh": None,
                    "topic": "tech",
                    "cefr_level": "B1",
                    "is_free": ep["is_free"],
                    "script_json": None,
                    "mp4_r2_key": ep["key"],
                    "audio_r2_key": None,
                    "has_delivery": user_id in ep["deliveries"],
                }
            ]
            return

        # /daily-orders/{date}/episode 對應的 join（find_delivered_episode）
        if "from public.deliveries d" in s and "join public.episodes e" in s:
            user_id, deliver_date = params[0], params[1]
            slugs = DELIVERIES_BY_DATE.get((user_id, deliver_date), [])
            self._rows = []
            for slug in slugs:
                ep = EPISODES.get(slug)
                if ep is None:
                    continue
                self._rows.append(
                    {
                        "slug": slug,
                        "title": f"T-{slug}",
                        "title_zh": None,
                        "topic": "tech",
                        "cefr_level": "B1",
                        "is_free": ep["is_free"],
                        "script_json": None,
                        "mp4_r2_key": ep["key"],
                        "audio_r2_key": None,
                    }
                )
            return

        # 其餘查詢測試不涉及，回空
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
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vocab_router, "connection", fake_connection)
    monkeypatch.setattr(episodes_router, "connection", fake_connection)
    monkeypatch.setattr(daily_orders_router, "connection", fake_connection)
    # repo.py 用 `from shared.db.pool import connection`，import 時把 connection
    # 綁進 db_repo 自己的 namespace；patch shared.db.pool 不夠，必須 patch db_repo。
    monkeypatch.setattr(db_repo, "connection", fake_connection)
    # presign 不打真 R2
    monkeypatch.setattr(
        episodes_router.r2,
        "presigned_get_url",
        lambda key, ttl=None: f"https://signed.example/{key}",
    )


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _token(user_id: str) -> str:
    settings = get_settings()
    return jwt.encode(
        {"sub": user_id, "aud": settings.supabase_jwt_audience},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id)}"}


# ── (a) 無 JWT → 401 ──────────────────────────────────────────────


def test_no_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/vocab")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_bad_jwt_returns_401(client: TestClient) -> None:
    res = client.get("/vocab", headers={"Authorization": "Bearer not-a-jwt"})
    assert res.status_code == 401
    assert res.json()["ok"] is False


# ── (b) 授權收斂：A 讀不到 B 的 vocab ──────────────────────────────


def test_vocab_scoped_to_owner(client: TestClient) -> None:
    res_a = client.get("/vocab", headers=_auth(USER_A))
    assert res_a.status_code == 200
    data_a = res_a.json()["data"]
    words_a = {v["word"] for v in data_a}
    assert words_a == {"serendipity"}
    assert "ephemeral" not in words_a  # 拿不到 B 的

    res_b = client.get("/vocab", headers=_auth(USER_B))
    words_b = {v["word"] for v in res_b.json()["data"]}
    assert words_b == {"ephemeral"}


# ── (c) ApiResponse envelope 形狀 ─────────────────────────────────


def test_envelope_shape_success(client: TestClient) -> None:
    body = client.get("/vocab", headers=_auth(USER_A)).json()
    assert set(body.keys()) == {"ok", "data", "error"}
    assert body["ok"] is True
    assert body["error"] is None
    assert isinstance(body["data"], list)
    # data 形狀 === 前端 VocabItem（camelCase）
    item = body["data"][0]
    assert "sourceEpisodeId" in item
    assert "nextReview" in item
    assert "createdAt" in item


def test_envelope_shape_error(client: TestClient) -> None:
    body = client.get("/vocab").json()
    assert set(body.keys()) == {"ok", "data", "error"}
    assert body["ok"] is False
    assert body["data"] is None
    assert set(body["error"].keys()) == {"code", "message"}


# ── (d) episodes/{slug}/url 授權 ──────────────────────────────────


def test_episode_url_free_ok(client: TestClient) -> None:
    res = client.get("/episodes/ep-free/url", headers=_auth(USER_A))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"].startswith("https://signed.example/")


def test_episode_url_delivered_ok(client: TestClient) -> None:
    res = client.get("/episodes/ep-a-only/url", headers=_auth(USER_A))
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_episode_url_locked_forbidden(client: TestClient) -> None:
    # A 對沒授權的集回 403
    res = client.get("/episodes/ep-locked/url", headers=_auth(USER_A))
    assert res.status_code == 403
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "forbidden"


def test_episode_url_delivered_to_other_forbidden(client: TestClient) -> None:
    # ep-a-only 只授權給 A；B 取應 403
    res = client.get("/episodes/ep-a-only/url", headers=_auth(USER_B))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"


def test_episode_url_unknown_slug_404(client: TestClient) -> None:
    res = client.get("/episodes/no-such-ep/url", headers=_auth(USER_A))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


# ── (e) /daily-orders/{date}/episode ──────────────────────────────


def test_get_delivered_episode_returns_slug(client: TestClient) -> None:
    res = client.get(
        "/daily-orders/2026-07-15/episode", headers=_auth(USER_A)
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"] is not None
    assert body["data"]["id"] == "ep-a-only"
    assert body["data"]["title"] == "T-ep-a-only"


def test_get_delivered_episode_null_when_no_delivery(client: TestClient) -> None:
    # USER_A 在 2026-07-16 沒交付記錄 → data 應為 null（不是 404，前端要 fallback）
    res = client.get(
        "/daily-orders/2026-07-16/episode", headers=_auth(USER_A)
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"] is None


def test_get_delivered_episode_owner_scoped(client: TestClient) -> None:
    # USER_B 查 USER_A 有交付的日期 → 200 + null（WHERE 過濾掉別人的交付）
    res = client.get(
        "/daily-orders/2026-07-15/episode", headers=_auth(USER_B)
    )
    assert res.status_code == 200
    assert res.json()["data"] is None


def test_get_delivered_episode_requires_auth(client: TestClient) -> None:
    res = client.get("/daily-orders/2026-07-15/episode")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"
