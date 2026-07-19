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

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.routers import activity as activity_router
from app.routers import daily_orders as daily_orders_router
from app.routers import episodes as episodes_router
from app.routers import vocab as vocab_router
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
    "ep-free": {"is_free": True, "deliveries": set(), "key": "media/ep-free.mp3"},
    "ep-a-only": {"is_free": False, "deliveries": {USER_A}, "key": "media/ep-a.mp3"},
    "ep-locked": {"is_free": False, "deliveries": set(), "key": "media/ep-locked.mp3"},
}

# (user_id, deliver_date) → 對應交付集數的 slug list。模擬 user 在指定日期的交付事實。
DELIVERIES_BY_DATE: dict[tuple[str, str], list[str]] = {
    (USER_A, "2026-07-15"): ["ep-a-only"],
}

# user_id → user_activity 假列（GET 直接查、PATCH read-modify-write 後覆寫）。
# 每個測試前由 _reset_activity fixture 清空，避免跨測試污染。
ACTIVITY_BY_USER: dict[str, dict[str, Any]] = {}


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
                    "audio_r2_key": ep["key"],
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
                        "audio_r2_key": ep["key"],
                    }
                )
            return

        # user_activity SELECT（GET /activity 與 PATCH 讀現況）
        if "from public.user_activity where user_id = %s" in s:
            user_id = params[0]
            row = ACTIVITY_BY_USER.get(user_id)
            self._rows = [row] if row is not None else []
            return

        # user_activity upsert（PATCH /activity；on conflict 那組參數與前半重複，取前半即可）
        if "insert into public.user_activity" in s:
            user_id = params[0]
            ACTIVITY_BY_USER[user_id] = {
                "streak_dates": json.loads(params[1]),
                "listen_minutes": json.loads(params[2]),
                "lookup_count": json.loads(params[3]),
                "listened_episode_ids": json.loads(params[4]),
                "last_played_episode_id": params[5],
                "last_played_position": params[6],
                "last_played_at": params[7],
            }
            self._rows = []
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
def _reset_activity() -> None:
    # ACTIVITY_BY_USER 會被 PATCH 測試就地寫入，測試間必須互相隔離。
    ACTIVITY_BY_USER.clear()


@pytest.fixture(autouse=True)
def patch_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vocab_router, "connection", fake_connection)
    monkeypatch.setattr(episodes_router, "connection", fake_connection)
    monkeypatch.setattr(daily_orders_router, "connection", fake_connection)
    monkeypatch.setattr(activity_router, "connection", fake_connection)
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
    from tests._auth import sign_test_token

    return sign_test_token(user_id)


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


# ── (f) /activity：學習進度上雲（T2）──────────────────────────────


def test_no_jwt_activity_401(client: TestClient) -> None:
    res_get = client.get("/activity")
    assert res_get.status_code == 401
    assert res_get.json()["ok"] is False
    assert res_get.json()["error"]["code"] == "unauthorized"

    res_patch = client.patch("/activity", json={"addStreakDate": "2026-07-16"})
    assert res_patch.status_code == 401
    assert res_patch.json()["ok"] is False
    assert res_patch.json()["error"]["code"] == "unauthorized"


def test_get_activity_default_when_no_row(client: TestClient) -> None:
    res = client.get("/activity", headers=_auth(USER_A))
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["streakDates"] == []
    assert data["listenMinutes"] == {}
    assert data["lookupCount"] == {}
    assert data["listenedEpisodeIds"] == []
    assert data["lastPlayedEpisodeId"] is None
    assert data["lastPlayedPosition"] is None
    assert data["lastPlayedAt"] is None


def test_patch_add_streak_date_dedup(client: TestClient) -> None:
    for _ in range(2):
        res = client.patch(
            "/activity", json={"addStreakDate": "2026-07-16"}, headers=_auth(USER_A)
        )
        assert res.status_code == 200
    data = client.get("/activity", headers=_auth(USER_A)).json()["data"]
    assert data["streakDates"] == ["2026-07-16"]


def test_patch_streak_dates_caps_365(client: TestClient) -> None:
    base = date(2025, 1, 1)
    existing = sorted((base + timedelta(days=i)).isoformat() for i in range(365))
    ACTIVITY_BY_USER[USER_A] = {
        "streak_dates": existing,
        "listen_minutes": {},
        "lookup_count": {},
        "listened_episode_ids": [],
        "last_played_episode_id": None,
        "last_played_position": None,
        "last_played_at": None,
    }
    res = client.patch(
        "/activity", json={"addStreakDate": "2027-01-01"}, headers=_auth(USER_A)
    )
    assert res.status_code == 200
    streak_dates = res.json()["data"]["streakDates"]
    assert len(streak_dates) == 365
    assert "2027-01-01" in streak_dates
    assert existing[0] not in streak_dates  # 最舊的被擠掉


def test_patch_add_listen_minutes_increments(client: TestClient) -> None:
    for _ in range(2):
        res = client.patch(
            "/activity",
            json={"addListenMinutes": {"month": "2026-07", "minutes": 5}},
            headers=_auth(USER_A),
        )
        assert res.status_code == 200
    data = client.get("/activity", headers=_auth(USER_A)).json()["data"]
    assert data["listenMinutes"] == {"2026-07": 10}


def test_patch_add_lookup_count_increments(client: TestClient) -> None:
    for _ in range(3):
        res = client.patch(
            "/activity",
            json={"addLookupCount": {"month": "2026-07", "count": 1}},
            headers=_auth(USER_A),
        )
        assert res.status_code == 200
    data = client.get("/activity", headers=_auth(USER_A)).json()["data"]
    assert data["lookupCount"] == {"2026-07": 3}


def test_patch_add_listened_episode_id_dedup(client: TestClient) -> None:
    for _ in range(2):
        res = client.patch(
            "/activity", json={"addListenedEpisodeId": "ep-free"}, headers=_auth(USER_A)
        )
        assert res.status_code == 200
    data = client.get("/activity", headers=_auth(USER_A)).json()["data"]
    assert data["listenedEpisodeIds"] == ["ep-free"]


def test_patch_last_played_newer_wins(client: TestClient) -> None:
    t1 = "2026-07-16T08:00:00Z"
    t2 = "2026-07-16T09:00:00Z"
    client.patch(
        "/activity",
        json={"lastPlayed": {"episodeId": "ep-a-only", "position": 10.0, "at": t1}},
        headers=_auth(USER_A),
    )
    res = client.patch(
        "/activity",
        json={"lastPlayed": {"episodeId": "ep-free", "position": 20.0, "at": t2}},
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    data = client.get("/activity", headers=_auth(USER_A)).json()["data"]
    assert data["lastPlayedEpisodeId"] == "ep-free"
    assert data["lastPlayedPosition"] == 20.0
    assert data["lastPlayedAt"] == t2


def test_patch_last_played_ignores_stale_out_of_order(client: TestClient) -> None:
    t1 = "2026-07-16T08:00:00Z"
    t2 = "2026-07-16T09:00:00Z"
    client.patch(
        "/activity",
        json={"lastPlayed": {"episodeId": "ep-free", "position": 20.0, "at": t2}},
        headers=_auth(USER_A),
    )
    res = client.patch(
        "/activity",
        json={"lastPlayed": {"episodeId": "ep-a-only", "position": 10.0, "at": t1}},
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    data = client.get("/activity", headers=_auth(USER_A)).json()["data"]
    # 較舊的節流請求（t1）不得覆蓋較新的進度（t2）
    assert data["lastPlayedEpisodeId"] == "ep-free"
    assert data["lastPlayedPosition"] == 20.0
    assert data["lastPlayedAt"] == t2


def test_activity_scoped_to_owner(client: TestClient) -> None:
    client.patch(
        "/activity",
        json={"addStreakDate": "2026-07-16", "addListenedEpisodeId": "ep-free"},
        headers=_auth(USER_A),
    )
    res_b = client.get("/activity", headers=_auth(USER_B))
    assert res_b.status_code == 200
    data_b = res_b.json()["data"]
    assert data_b["streakDates"] == []
    assert data_b["listenedEpisodeIds"] == []


def test_patch_activity_envelope_shape(client: TestClient) -> None:
    res = client.patch(
        "/activity", json={"addStreakDate": "2026-07-16"}, headers=_auth(USER_A)
    )
    body = res.json()
    assert set(body.keys()) == {"ok", "data", "error"}
    assert body["ok"] is True
    assert body["error"] is None
    data = body["data"]
    assert set(data.keys()) == {
        "streakDates",
        "listenMinutes",
        "lookupCount",
        "listenedEpisodeIds",
        "lastPlayedEpisodeId",
        "lastPlayedPosition",
        "lastPlayedAt",
    }


def test_patch_activity_partial_body_untouched_fields(client: TestClient) -> None:
    client.patch(
        "/activity",
        json={
            "addStreakDate": "2026-07-16",
            "addListenedEpisodeId": "ep-free",
            "addListenMinutes": {"month": "2026-07", "minutes": 5},
            "lastPlayed": {"episodeId": "ep-free", "position": 10.0, "at": "2026-07-16T08:00:00Z"},
        },
        headers=_auth(USER_A),
    )
    res = client.patch(
        "/activity",
        json={"addLookupCount": {"month": "2026-07", "count": 1}},
        headers=_auth(USER_A),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["streakDates"] == ["2026-07-16"]
    assert data["listenedEpisodeIds"] == ["ep-free"]
    assert data["listenMinutes"] == {"2026-07": 5}
    assert data["lastPlayedEpisodeId"] == "ep-free"
    assert data["lookupCount"] == {"2026-07": 1}
