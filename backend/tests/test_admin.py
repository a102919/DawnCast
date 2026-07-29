"""Admin / ops endpoint 測試（T7）。

授權機制：唯一路徑＝Supabase JWT（Google 登入）email claim 對上 ADMIN_EMAIL
（見 app/routers/admin.py require_admin）。X-Admin-Token 後門已於 2026-07-29
砍掉，改用雙保險：email_verified=True + app_metadata.provider=google + email
命中白名單。

故自成一份測試檔、自帶 FakeConnection，不共用 test_api.py 的 patch_db fixture。

驗證重點：
  (a) 帶正確 Google JWT → 200，資料形狀正確（camelCase）
  (b) 不帶 / 帶錯 token → 401
  (c) 帶合法 Supabase JWT 但 ADMIN_EMAIL 未設定 / email 不符 → 仍 401
  (d) ADMIN_EMAIL 已設定且 JWT email 相符（大小寫不敏感）→ 200
  (e) ADMIN_EMAIL 未設定時 fail-closed，一律拒絕
  (f) email_verified != True / provider != google 一律拒絕
  (g) 偽造簽章 / 亂 kid / alg=none / 缺 exp claim 攻擊面一律拒絕
"""

from __future__ import annotations

import base64
import itertools
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from app import deps as deps_mod
from app.routers import admin as admin_router
from shared.config import Settings
from tests._auth import _KID, _ensure_init  # type: ignore[attr-defined]
from tests._db_fakes import FakeConnection as _BaseFakeConnection
from tests._db_fakes import FakeCursor as _BaseFakeCursor
from tests._db_fakes import fake_connection

ADMIN_EMAIL = "admin@example.com"

# PNG/JPEG/WebP magic bytes（後面補任意 padding，驗證只看開頭，不解碼真圖）。
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 16
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16

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

# listener_count／favorite_count 刻意給非零、彼此不同的數字：如果 router
# 把欄位接錯或漏傳，預設值 0 會讓斷言誤判通過；兩個數字不同才能抓出
# 「listener_count 誤接成 favorite_count」這類接錯欄位的情況。
_EPISODE_STATS_ROWS: list[dict[str, Any]] = [
    {
        "id": "ep-2",
        "title": "Episode 2",
        "topic": "tech",
        "cefr_level": "B1",
        "is_free": False,
        "episode_no": 2,
        "published_at": "2026-07-16",
        "created_at": "2026-07-16T00:00:00Z",
        "channel_name": "AI 頻道",
        "has_audio": True,
        "play_count": 42,
        "input_tokens": 500,
        "output_tokens": 300,
        "wall_ms": 362000,
        "stages": [
            {"node": "write_script", "duration_ms": 12000, "status": "ok", "attempt": 1},
            {"node": "render_episode", "duration_ms": 350000, "status": "ok", "attempt": 1},
        ],
        "listener_count": 7,
        "favorite_count": 3,
    },
    {
        "id": "ep-1",
        "title": "Episode 1",
        "topic": "news",
        "cefr_level": "A2",
        "is_free": True,
        "episode_no": 1,
        "published_at": "2026-07-15",
        "created_at": "2026-07-15T00:00:00Z",
        "channel_name": None,
        "has_audio": False,
        "play_count": 0,
        "input_tokens": 200,
        "output_tokens": 100,
        "wall_ms": None,
        "stages": [],
        "listener_count": 0,
        "favorite_count": 0,
    },
]


class FakeCursor(_BaseFakeCursor):
    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        s = " ".join(sql.split())  # 正規化空白

        if "coalesce(sum(input_tokens)" in s:
            total_input = sum(r["input_tokens"] for r in _EPISODE_STATS_ROWS)
            total_output = sum(r["output_tokens"] for r in _EPISODE_STATS_ROWS)
            total_play = sum(r["play_count"] for r in _EPISODE_STATS_ROWS)
            self._rows = [
                {
                    "total_input_tokens": total_input,
                    "total_output_tokens": total_output,
                    "total_play_count": total_play,
                    "episode_count": len(_EPISODE_STATS_ROWS),
                }
            ]
            return

        if "has_audio" in s and "from public.episodes e" in s:
            self._rows = list(_EPISODE_STATS_ROWS)
            return

        if "pgmq.metrics_all" in s:
            self._rows = list(_JOB_ROWS)
            return

        self._rows = []
        return


class FakeConnection(_BaseFakeConnection):
    def cursor(self, **_: object) -> FakeCursor:
        return FakeCursor()


SENT_MESSAGES: list[tuple[str, dict[str, Any]]] = []
QUEUE_MSG_ID = 4242


async def spy_queue_send(queue_name: str, body: dict[str, Any]) -> int:
    SENT_MESSAGES.append((queue_name, dict(body)))
    return QUEUE_MSG_ID


def _today_in_app_tz() -> str:
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI

    return _dt.now(_ZI("Asia/Taipei")).date().isoformat()


# ── 頻道機制：channels_db 函式級別 fake（該模組本身有自己的 test_channels_repo.py
# 顧 SQL 正確性，這裡只驗 router 邏輯——auth／驗證／404／正確呼叫順序／回應形狀，
# 不重新模擬一份 SQL cursor 分派）────────────────────────────────────────

_CHANNELS: dict[str, dict[str, Any]] = {}
_CHANNEL_TOPICS: dict[str, dict[str, Any]] = {}
_NEXT_ID = itertools.count(1)
COVER_PUT_CALLS: list[tuple[str, bytes, str]] = []


def _seed_channel(**overrides: Any) -> dict[str, Any]:
    channel_id = overrides.pop("id", None) or f"chan-{next(_NEXT_ID)}"
    row: dict[str, Any] = {
        "id": channel_id,
        "slug": "test-channel",
        "name": "測試頻道",
        "description": None,
        "theme_prompt": "系統提示",
        "topic": "tech",
        "topic_type": "evergreen",
        "length_tier": "medium",
        "cefr_level": "B1",
        "target_interval_days": 3,
        "status": "active",
        "cover_r2_key": None,
        "last_published_at": None,
        "created_at": "2026-07-16T00:00:00Z",
        "updated_at": "2026-07-16T00:00:00Z",
        "episode_count": 0,
        "candidate_count": 0,
    }
    row.update(overrides)
    _CHANNELS[channel_id] = row
    return row


def _seed_topic(channel_id: str, **overrides: Any) -> dict[str, Any]:
    topic_id = overrides.pop("id", None) or f"topic-{next(_NEXT_ID)}"
    row: dict[str, Any] = {
        "id": topic_id,
        "channel_id": channel_id,
        "canonical_topic": "測試選題",
        "angle": "定義",
        "rationale": None,
        "score": 0.8,
        "status": "candidate",
        "parent_episode_id": None,
        "episode_id": None,
        "created_at": "2026-07-16T00:00:00Z",
        "decided_at": None,
    }
    row.update(overrides)
    _CHANNEL_TOPICS[topic_id] = row
    return row


async def fake_list_channels(*, status: str | None = None) -> list[dict[str, Any]]:
    rows = list(_CHANNELS.values())
    if status is not None:
        rows = [r for r in rows if r["status"] == status]
    return sorted(rows, key=lambda r: r["created_at"], reverse=True)


async def fake_get_channel(channel_id: str) -> dict[str, Any] | None:
    return _CHANNELS.get(channel_id)


async def fake_create_channel(
    *,
    slug: str,
    name: str,
    theme_prompt: str,
    topic: str,
    description: str | None = None,
    topic_type: str = "evergreen",
    length_tier: str = "medium",
    cefr_level: str = "B1",
    target_interval_days: int = 3,
    status: str = "active",
) -> str:
    row = _seed_channel(
        slug=slug,
        name=name,
        theme_prompt=theme_prompt,
        topic=topic,
        description=description,
        topic_type=topic_type,
        length_tier=length_tier,
        cefr_level=cefr_level,
        target_interval_days=target_interval_days,
        status=status,
    )
    return str(row["id"])


async def fake_update_channel(channel_id: str, **fields: Any) -> bool:
    row = _CHANNELS.get(channel_id)
    if row is None:
        return False
    row.update(fields)
    return True


async def fake_set_channel_cover(channel_id: str, r2_key: str) -> None:
    row = _CHANNELS.get(channel_id)
    if row is not None:
        row["cover_r2_key"] = r2_key


async def fake_list_channel_topics(
    channel_id: str, *, status: str | None = None
) -> list[dict[str, Any]]:
    rows = [r for r in _CHANNEL_TOPICS.values() if r["channel_id"] == channel_id]
    if status is not None:
        rows = [r for r in rows if r["status"] == status]
    return sorted(rows, key=lambda r: r["score"], reverse=True)


async def fake_update_topic_status(
    topic_id: str, status: str, *, episode_id: str | None = None
) -> bool:
    row = _CHANNEL_TOPICS.get(topic_id)
    if row is None:
        return False
    row["status"] = status
    if episode_id is not None:
        row["episode_id"] = episode_id
    return True


async def fake_rename_channel_topic(topic_id: str, canonical_topic: str) -> None:
    row = _CHANNEL_TOPICS.get(topic_id)
    if row is not None:
        row["canonical_topic"] = canonical_topic


def _fake_presigned_get_url(key: str, ttl: int | None = None) -> str:
    return f"https://signed.example/{key}"


def _fake_presigned_get_urls(keys: list[str], ttl: int | None = None) -> dict[str, str]:
    return {k: _fake_presigned_get_url(k) for k in keys}


def _spy_put_object(key: str, data: bytes, content_type: str) -> None:
    COVER_PUT_CALLS.append((key, data, content_type))


@pytest.fixture(autouse=True)
def patch_admin_db(monkeypatch: pytest.MonkeyPatch) -> None:
    SENT_MESSAGES.clear()
    _CHANNELS.clear()
    _CHANNEL_TOPICS.clear()
    COVER_PUT_CALLS.clear()
    monkeypatch.setattr(admin_router, "connection", fake_connection(FakeConnection))
    monkeypatch.setattr(admin_router.queue, "send", spy_queue_send)
    monkeypatch.setattr(admin_router.channels_db, "list_channels", fake_list_channels)
    monkeypatch.setattr(admin_router.channels_db, "get_channel", fake_get_channel)
    monkeypatch.setattr(admin_router.channels_db, "create_channel", fake_create_channel)
    monkeypatch.setattr(admin_router.channels_db, "update_channel", fake_update_channel)
    monkeypatch.setattr(admin_router.channels_db, "set_channel_cover", fake_set_channel_cover)
    monkeypatch.setattr(admin_router.channels_db, "list_channel_topics", fake_list_channel_topics)
    monkeypatch.setattr(admin_router.channels_db, "update_topic_status", fake_update_topic_status)
    monkeypatch.setattr(admin_router, "_rename_channel_topic", fake_rename_channel_topic)
    monkeypatch.setattr(admin_router.r2, "presigned_get_url", _fake_presigned_get_url)
    monkeypatch.setattr(admin_router.r2, "presigned_get_urls", _fake_presigned_get_urls)
    monkeypatch.setattr(admin_router.r2, "put_object", _spy_put_object)
    # 獨立於全域 get_settings() 的 lru_cache 單例，避免污染其他測試檔。
    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email=ADMIN_EMAIL),
    )


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _jwt_admin_headers() -> dict[str, str]:
    """admin 授權唯一路徑：Google OAuth 拿到的 JWT email 命中白名單。"""
    from tests._auth import auth_header

    return auth_header("some-user-id", email=ADMIN_EMAIL)


def _jwt_headers() -> dict[str, str]:
    """沒命中 email 白名單的合法 JWT（admin_email='admin@example.com'、JWT
    email='someone-else@example.com'），用於驗「帶錯 email 仍 401」。"""
    from tests._auth import auth_header

    return auth_header("some-user-id", email="someone-else@example.com")


# ── /admin/episodes ────────────────────────────────────────────────


def test_episodes_no_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/episodes")
    assert res.status_code == 401
    body = res.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_episodes_correct_token_returns_200(client: TestClient) -> None:
    """單集數據總覽：彙總數字 + 明細（播放／聽完／收藏／token／耗時全部到位）。"""
    res = client.get("/admin/episodes", headers=_jwt_admin_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["episodeCount"] == 2
    assert data["totalInputTokens"] == 700
    assert data["totalOutputTokens"] == 400
    assert data["totalPlayCount"] == 42

    items = data["items"]
    assert [i["id"] for i in items] == ["ep-2", "ep-1"]  # createdAt desc
    ep2 = items[0]
    assert ep2["hasAudio"] is True
    assert ep2["channelName"] == "AI 頻道"
    assert ep2["playCount"] == 42
    # listener/favorite 給的是非零、彼此不同的數字（見 fixture 註解），
    # 兩者都要對得上才代表 router 沒把欄位接錯。
    assert ep2["listenerCount"] == 7
    assert ep2["favoriteCount"] == 3
    # ep-2 有 gen_metrics.stages → 攤平進 AdminEpisodeStats.stages
    assert [s["node"] for s in ep2["stages"]] == ["write_script", "render_episode"]
    assert ep2["wallMs"] == 362000

    ep1 = items[1]
    # ep-1 沒有 gen_metrics（舊集數 / migration 前）→ 空 list + None，不是缺欄位
    assert ep1["stages"] == []
    assert ep1["wallMs"] is None
    assert ep1["listenerCount"] == 0
    assert ep1["favoriteCount"] == 0
    assert ep1["channelName"] is None


# ── /admin/jobs ───────────────────────────────────────────────────


def test_jobs_no_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/jobs")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_jobs_correct_token_returns_200(client: TestClient) -> None:
    res = client.get("/admin/jobs", headers=_jwt_admin_headers())
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


# ── 授權收斂：JWT 沒命中 admin_email 必 401 ──────────────────────────────


def test_jwt_email_not_in_allowlist_still_401(client: TestClient) -> None:
    """JWT 合法 + email 沒命中 ADMIN_EMAIL 白名單 → 401（fail-closed）。"""
    res = client.get("/admin/episodes", headers=_jwt_headers())
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_admin_email_unset_denies_jwt_even_with_email_claim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """admin_email 為空字串：帶 email claim 的合法 JWT 仍不能開後台。"""
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router, "get_settings", lambda: Settings(environment="dev", admin_email="")
    )
    res = client.get(
        "/admin/episodes", headers=auth_header("some-user-id", email="admin@example.com")
    )
    assert res.status_code == 401


def test_admin_email_matching_jwt_allows_access(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="Admin@Example.com"),
    )
    res = client.get(
        "/admin/episodes", headers=auth_header("some-user-id", email="admin@example.com")
    )
    assert res.status_code == 200


def test_admin_email_mismatched_jwt_still_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    res = client.get(
        "/admin/episodes", headers=auth_header("some-user-id", email="someone-else@example.com")
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


# ── 第二輪稽核補的攻擊情境測試（見 audit-agent 報告）────────────────────


def test_admin_email_unverified_jwt_still_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """email_verified != True 一律拒：避免 Supabase Email provider 開放自助註冊時
    攻擊者用 admin email 自己註冊拿到合法 JWT（見 _is_authorized_admin）。

    真實 Supabase JWT 把 email_verified 放在 user_metadata.email_verified 巢狀，
    fixture 必須對齊此結構，否則測試綠但 prod 401（見 _is_authorized_admin）。"""
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    res = client.get(
        "/admin/episodes",
        headers=auth_header(
            "some-user-id",
            email="admin@example.com",
            user_metadata={"email_verified": False},
        ),
    )
    assert res.status_code == 401


def test_admin_email_missing_user_metadata_still_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沒帶 user_metadata 的 JWT：對齊真實 Supabase 結構必帶 user_metadata 但裡面
    不一定有 email_verified；要求顯式 True 才能通過，不能 fallback 通過。

    對應攻擊面：若 attacker 找到缺 user_metadata 也能 fall-through 通過的 bug，
    就能拿下 admin。"""
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    res = client.get(
        "/admin/episodes",
        headers=auth_header(
            "some-user-id",
            email="admin@example.com",
            user_metadata={},
        ),
    )
    assert res.status_code == 401


def test_admin_email_top_level_verified_ignored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只有 top-level email_verified=True、user_metadata.email_verified 沒設 →
    仍 401。守住「後端只信 nested 結構」這條 invariant。若未來有人改成 fallback
    接受 top-level，這條測試會紅。"""
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    res = client.get(
        "/admin/episodes",
        headers=auth_header(
            "some-user-id",
            email="admin@example.com",
            email_verified=True,
            user_metadata={},
        ),
    )
    assert res.status_code == 401


def test_admin_jwt_matches_real_supabase_structure_allows_access(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """happy path：對齊真實 Supabase JWT 結構（email_verified 巢狀在 user_metadata）
    + email 命中 + google provider → 200。"""
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    res = client.get(
        "/admin/episodes",
        headers=auth_header(
            "some-user-id",
            email="admin@example.com",
            user_metadata={"email_verified": True, "full_name": "Alan Tsai"},
            app_metadata={"provider": "google", "providers": ["google"]},
        ),
    )
    assert res.status_code == 200


def test_admin_email_non_google_provider_still_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """app_metadata.provider != "google" 一律拒：確保只有 Google OAuth 登入的帳號
    能開後台，email/password 註冊或 magiclink 等其他 provider 拿不到 admin。"""
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    res = client.get(
        "/admin/episodes",
        headers=auth_header(
            "some-user-id",
            email="admin@example.com",
            app_metadata={"provider": "email"},
        ),
    )
    assert res.status_code == 401


def test_admin_jwt_missing_exp_claim_still_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """沒帶 exp claim 的合法簽章 JWT 一律拒：python-jose 預設只驗 exp 合法性
    不要求存在；admin 入口強制 require_exp，免費防禦長效 token。"""
    _ensure_init()
    # _priv_pem 是 module-level mutable，from import 只在 load 時取值一次，
    # 這裡透過 tests._auth module attribute 重新讀，確保拿到 _ensure_init 後的值。
    from tests import _auth

    assert _auth._priv_pem is not None
    priv_pem = _auth._priv_pem

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    # 不放 exp claim，照樣 ES256 簽（sign_test_token 預設會帶 exp=9999999999，
    # 這裡刻意覆寫 pop 把整個 claim 拿掉——jose 不讓 exp 為 None encode）
    payload = {
        "sub": "some-user-id",
        "aud": "authenticated",
        "email": "admin@example.com",
        "email_verified": True,
        "app_metadata": {"provider": "google"},
    }
    token = str(jose_jwt.encode(payload, priv_pem, algorithm="ES256", headers={"kid": _KID}))
    res = client.get("/admin/episodes", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_admin_jwt_wrong_signature_with_valid_kid_still_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自己 key 簽 + 真 kid 的偽造 token → 401：證明 email claim 真在簽章覆蓋
    範圍內，攻擊者無法繞 JWKS 白名單用自己 key 偷渡任意 email。"""

    rogue_priv = ec.generate_private_key(ec.SECP256R1())
    rogue_pem = rogue_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    payload = {
        "sub": "rogue",
        "aud": "authenticated",
        "email": "admin@example.com",
        "email_verified": True,
        "app_metadata": {"provider": "google"},
        "exp": 9999999999,
    }
    token = str(jose_jwt.encode(payload, rogue_pem, algorithm="ES256", headers={"kid": _KID}))
    res = client.get("/admin/episodes", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_admin_jwt_no_email_claim_still_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """合法 JWT 沒 email claim → 401。"""
    from tests._auth import auth_header

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    res = client.get("/admin/episodes", headers=auth_header("some-user-id"))
    assert res.status_code == 401


def test_admin_jwt_alg_none_still_401(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """alg=none 的偽造 token → 401：jwt.decode 的 algorithms 白名單會拒絕。"""
    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )
    # 手刻 alg=none 的 JWT（jose 不讓你這樣 encode）
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "kid": "x"}).encode()).rstrip(b"=")
    body = base64.urlsafe_b64encode(
        json.dumps(
            {
                "sub": "rogue",
                "aud": "authenticated",
                "email": "admin@example.com",
                "email_verified": True,
                "app_metadata": {"provider": "google"},
                "exp": 9999999999,
            }
        ).encode()
    ).rstrip(b"=")
    token = (header + b"." + body + b".").decode()
    res = client.get("/admin/episodes", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_admin_jwt_random_kid_does_not_hammer_jwks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """亂 kid 不該無限次強迫 JWKS 重抓——冷卻期間第二次直接 401，不打外網。

    用 monkeypatch 計數 JWKS factory 被呼叫次數，斷言 N 個亂 kid 只觸發 1 次
    外網 fetch。
    """
    # 保存原值，test 結束（即使斷言失敗）都要還原 _jwks_factory 與冷卻狀態，
    # 否則同一個 process 內後續測試 decode JWT 會撞到計數 factory 或撞冷卻 401。
    monkeypatch.setattr(deps_mod, "_jwks_last_forced_refetch", 0.0)
    monkeypatch.setattr(deps_mod, "_jwks_cache", None)
    monkeypatch.setattr(deps_mod, "_jwks_fetched_at", 0.0)
    fetch_count = {"n": 0}

    def counting_factory(_settings: object) -> dict[str, object]:
        fetch_count["n"] += 1
        # JWKS 故意是空集合：所有 kid 都 miss，模擬「bad kid」
        return {"keys": []}

    monkeypatch.setattr(deps_mod, "_jwks_factory", counting_factory)
    deps_mod._invalidate_jwks_cache()

    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email="admin@example.com"),
    )

    # 用 jose 直接造一支 ES256 token，kid 故意填 JWKS 沒有的值
    rogue_priv = ec.generate_private_key(ec.SECP256R1())
    rogue_pem = rogue_priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    for kid in ("bogus-1", "bogus-2", "bogus-3"):
        payload = {
            "sub": "u",
            "aud": "authenticated",
            "email": "admin@example.com",
            "email_verified": True,
            "app_metadata": {"provider": "google"},
            "exp": 9999999999,
        }
        token = str(jose_jwt.encode(payload, rogue_pem, algorithm="ES256", headers={"kid": kid}))
        res = client.get("/admin/episodes", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 401

    # 冷卻前第一個請求 → invalidate + 重抓一次（fetch=1）
    # 後續兩個 → 冷卻命中，不重抓（fetch 仍 =1）
    assert fetch_count["n"] == 1, f"亂 kid 應該只觸發一次 JWKS 重抓，實際 {fetch_count['n']} 次"


# ── /admin/eps/generate ────────────────────────────────────────────


def test_eps_generate_minimal_body_enqueues_generate_queue(client: TestClient) -> None:
    res = client.post(
        "/admin/eps/generate",
        json={"topic": "AI"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 202
    today = _today_in_app_tz()
    assert res.json() == {
        "ok": True,
        "data": {
            "idempotencyKey": f"{today}:AI:定義:medium:evergreen",
            "msgId": QUEUE_MSG_ID,
            "status": "queued",
        },
        "error": None,
    }
    assert [
        (
            "generate",
            {
                "big_topic": "AI",
                "angle": "定義",
                "cluster_id": None,
                "deliver_date": today,
                "user_ids": [],
                "length_tier": "medium",
                "cefr": "B1",
                "source": "fallback",
                "topic_type": "evergreen",
            },
        )
    ] == SENT_MESSAGES


def test_eps_generate_with_all_options_enqueues_full_payload(client: TestClient) -> None:
    res = client.post(
        "/admin/eps/generate",
        json={
            "topic": "太空探索",
            "angle": "對比",
            "topicType": "news",
            "lengthTier": "long",
            "cefr": "B2",
            "userIds": ["user-a", "user-b"],
            "deliverDate": "2026-08-01",
        },
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 202
    assert res.json()["data"]["idempotencyKey"] == "2026-08-01:太空探索:對比:long:news"
    assert SENT_MESSAGES == [
        (
            "generate",
            {
                "big_topic": "太空探索",
                "angle": "對比",
                "cluster_id": None,
                "deliver_date": "2026-08-01",
                "user_ids": ["user-a", "user-b"],
                "length_tier": "long",
                "cefr": "B2",
                "source": "fallback",
                "topic_type": "news",
            },
        )
    ]


def test_eps_generate_without_auth_returns_401(client: TestClient) -> None:
    res = client.post("/admin/eps/generate", json={"topic": "AI"})
    assert res.status_code == 401
    assert res.json()["ok"] is False
    assert res.json()["error"]["code"] == "unauthorized"
    assert SENT_MESSAGES == []


def test_eps_generate_invalid_angle_returns_400(client: TestClient) -> None:
    res = client.post(
        "/admin/eps/generate",
        json={"topic": "AI", "angle": "不存在的角度"},
        headers=_jwt_admin_headers(),
    )
    # 全站 validation handler 回 400，見 app/main.py
    assert res.status_code == 400
    assert res.json()["ok"] is False
    assert res.json()["error"]["code"] == "validation_error"
    assert SENT_MESSAGES == []


def test_eps_generate_invalid_topic_type_returns_400(client: TestClient) -> None:
    res = client.post(
        "/admin/eps/generate",
        json={"topic": "AI", "topicType": "invalid"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert SENT_MESSAGES == []


# ── 授權收斂：每個新頻道 endpoint 都要各自驗一次 401 ─────────────────────


def test_list_channels_no_token_returns_401(client: TestClient) -> None:
    res = client.get("/admin/channels")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_create_channel_no_token_returns_401(client: TestClient) -> None:
    res = client.post(
        "/admin/channels",
        json={"slug": "a", "name": "A", "themePrompt": "p", "topic": "tech"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_update_channel_no_token_returns_401(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    res = client.patch("/admin/channels/chan-1", json={"name": "New"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_list_channel_topics_no_token_returns_401(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    res = client.get("/admin/channels/chan-1/topics")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_update_channel_topic_no_token_returns_401(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    _seed_topic("chan-1", id="topic-1")
    res = client.patch("/admin/channels/chan-1/topics/topic-1", json={"status": "rejected"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"


def test_plan_channel_no_token_returns_401(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    res = client.post("/admin/channels/chan-1/plan")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"
    assert SENT_MESSAGES == []


def test_upload_cover_no_token_returns_401(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    res = client.post(
        "/admin/channels/chan-1/cover",
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"
    assert COVER_PUT_CALLS == []


# ── GET / POST / PATCH /admin/channels ──────────────────────────────


def test_list_channels_returns_seeded_rows(client: TestClient) -> None:
    _seed_channel(id="chan-1", slug="tech-daily", name="科技日報", status="active")
    _seed_channel(id="chan-2", slug="biz-weekly", name="商業週報", status="paused")

    res = client.get("/admin/channels", headers=_jwt_admin_headers())
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert {c["id"] for c in body["data"]} == {"chan-1", "chan-2"}
    item = next(c for c in body["data"] if c["id"] == "chan-1")
    assert item["slug"] == "tech-daily"
    assert item["themePrompt"] == "系統提示"
    assert item["coverImageUrl"] is None  # 沒設封面


def test_list_channels_filters_by_status(client: TestClient) -> None:
    _seed_channel(id="chan-1", status="active")
    _seed_channel(id="chan-2", status="paused")

    res = client.get("/admin/channels", params={"status": "active"}, headers=_jwt_admin_headers())
    assert res.status_code == 200
    data = res.json()["data"]
    assert [c["id"] for c in data] == ["chan-1"]


def test_list_channels_signs_cover_url_when_set(client: TestClient) -> None:
    _seed_channel(id="chan-1", cover_r2_key="channels/chan-1/cover.png")

    res = client.get("/admin/channels", headers=_jwt_admin_headers())
    assert res.status_code == 200
    item = res.json()["data"][0]
    assert item["coverImageUrl"] == "https://signed.example/channels/chan-1/cover.png"


def test_create_channel_valid_body_returns_200(client: TestClient) -> None:
    res = client.post(
        "/admin/channels",
        json={
            "slug": "tech-daily",
            "name": "科技日報",
            "themePrompt": "每天一個科技主題，適合通勤收聽",
            "topic": "tech",
        },
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["slug"] == "tech-daily"
    assert data["topicType"] == "evergreen"  # 預設值
    assert data["lengthTier"] == "medium"
    assert data["cefrLevel"] == "B1"
    assert data["targetIntervalDays"] == 3
    assert data["status"] == "active"
    assert data["episodeCount"] == 0
    assert data["candidateCount"] == 0
    assert data["id"] in _CHANNELS


def test_create_channel_invalid_slug_returns_400(client: TestClient) -> None:
    res = client.post(
        "/admin/channels",
        json={"slug": "Not Valid Slug!", "name": "A", "themePrompt": "p", "topic": "tech"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert _CHANNELS == {}


def test_create_channel_invalid_topic_returns_400(client: TestClient) -> None:
    res = client.post(
        "/admin/channels",
        json={"slug": "a", "name": "A", "themePrompt": "p", "topic": "not-a-real-topic"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert _CHANNELS == {}


def test_create_channel_target_interval_days_out_of_range_returns_400(
    client: TestClient,
) -> None:
    res = client.post(
        "/admin/channels",
        json={
            "slug": "a",
            "name": "A",
            "themePrompt": "p",
            "topic": "tech",
            "targetIntervalDays": 31,
        },
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert _CHANNELS == {}


def test_update_channel_partial_update_only_touches_given_fields(
    client: TestClient,
) -> None:
    _seed_channel(id="chan-1", name="舊名稱", status="active", slug="old-slug")

    res = client.patch(
        "/admin/channels/chan-1",
        json={"name": "新名稱"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["name"] == "新名稱"
    assert data["slug"] == "old-slug"  # 沒送的欄位不變
    assert data["status"] == "active"


def test_update_channel_not_found_returns_404(client: TestClient) -> None:
    res = client.patch(
        "/admin/channels/does-not-exist",
        json={"name": "新名稱"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_update_channel_invalid_status_returns_400(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    res = client.patch(
        "/admin/channels/chan-1",
        json={"status": "not-a-real-status"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


# ── 選題庫 ────────────────────────────────────────────────────────


def test_list_channel_topics_returns_seeded_rows(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    _seed_topic("chan-1", id="topic-1", canonical_topic="A 主題", status="candidate", score=0.9)
    _seed_topic("chan-1", id="topic-2", canonical_topic="B 主題", status="rejected", score=0.5)

    res = client.get("/admin/channels/chan-1/topics", headers=_jwt_admin_headers())
    assert res.status_code == 200
    data = res.json()["data"]
    assert [t["id"] for t in data] == ["topic-1", "topic-2"]  # score desc


def test_list_channel_topics_filters_by_status(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    _seed_topic("chan-1", id="topic-1", status="candidate")
    _seed_topic("chan-1", id="topic-2", status="rejected")

    res = client.get(
        "/admin/channels/chan-1/topics",
        params={"status": "rejected"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert [t["id"] for t in data] == ["topic-2"]


def test_list_channel_topics_channel_not_found_returns_404(client: TestClient) -> None:
    res = client.get("/admin/channels/does-not-exist/topics", headers=_jwt_admin_headers())
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_update_channel_topic_status_to_rejected(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    _seed_topic("chan-1", id="topic-1", status="candidate")

    res = client.patch(
        "/admin/channels/chan-1/topics/topic-1",
        json={"status": "rejected"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 200
    assert res.json()["data"]["status"] == "rejected"
    assert _CHANNEL_TOPICS["topic-1"]["status"] == "rejected"


def test_update_channel_topic_rename_canonical_topic(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    _seed_topic("chan-1", id="topic-1", canonical_topic="舊主題文字")

    res = client.patch(
        "/admin/channels/chan-1/topics/topic-1",
        json={"canonicalTopic": "修正後的主題文字"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 200
    assert res.json()["data"]["canonicalTopic"] == "修正後的主題文字"


def test_update_channel_topic_invalid_status_returns_400(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    _seed_topic("chan-1", id="topic-1", status="candidate")

    res = client.patch(
        "/admin/channels/chan-1/topics/topic-1",
        json={"status": "published"},  # 只允許 candidate/rejected，其餘轉移由 pipeline 管
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert _CHANNEL_TOPICS["topic-1"]["status"] == "candidate"


def test_update_channel_topic_not_found_returns_404(client: TestClient) -> None:
    _seed_channel(id="chan-1")
    res = client.patch(
        "/admin/channels/chan-1/topics/does-not-exist",
        json={"status": "rejected"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"


def test_update_channel_topic_wrong_channel_returns_404_and_does_not_mutate(
    client: TestClient,
) -> None:
    """topic 屬於 chan-A，URL 卻帶 chan-B——必須 404，且不能把別頻道的選題改掉。"""
    _seed_channel(id="chan-a")
    _seed_channel(id="chan-b")
    _seed_topic("chan-a", id="topic-1", status="candidate")

    res = client.patch(
        "/admin/channels/chan-b/topics/topic-1",
        json={"status": "rejected"},
        headers=_jwt_admin_headers(),
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert _CHANNEL_TOPICS["topic-1"]["status"] == "candidate"  # 沒被誤改


# ── POST /admin/channels/{channel_id}/plan ──────────────────────────


def test_plan_channel_returns_202_and_enqueues_control_queue(client: TestClient) -> None:
    _seed_channel(id="chan-1")

    res = client.post("/admin/channels/chan-1/plan", headers=_jwt_admin_headers())
    assert res.status_code == 202
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["channelId"] == "chan-1"
    assert body["data"]["status"] == "queued"
    assert SENT_MESSAGES == [("control", {"task": "channel_plan", "channel_id": "chan-1"})]


def test_plan_channel_not_found_returns_404(client: TestClient) -> None:
    res = client.post("/admin/channels/does-not-exist/plan", headers=_jwt_admin_headers())
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert SENT_MESSAGES == []


# ── POST /admin/channels/{channel_id}/cover ──────────────────────────


def test_upload_cover_valid_png_returns_200_and_calls_put_object(client: TestClient) -> None:
    _seed_channel(id="chan-1")

    res = client.post(
        "/admin/channels/chan-1/cover",
        content=PNG_BYTES,
        headers={**_jwt_admin_headers(), "Content-Type": "image/png"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["data"]["coverImageUrl"] == "https://signed.example/channels/chan-1/cover.png"
    assert COVER_PUT_CALLS == [("channels/chan-1/cover.png", PNG_BYTES, "image/png")]
    assert _CHANNELS["chan-1"]["cover_r2_key"] == "channels/chan-1/cover.png"


def test_upload_cover_valid_jpeg_returns_200(client: TestClient) -> None:
    _seed_channel(id="chan-1")

    res = client.post(
        "/admin/channels/chan-1/cover",
        content=JPEG_BYTES,
        headers={**_jwt_admin_headers(), "Content-Type": "image/jpeg"},
    )
    assert res.status_code == 200
    assert COVER_PUT_CALLS == [("channels/chan-1/cover.jpg", JPEG_BYTES, "image/jpeg")]


def test_upload_cover_valid_webp_returns_200(client: TestClient) -> None:
    _seed_channel(id="chan-1")

    res = client.post(
        "/admin/channels/chan-1/cover",
        content=WEBP_BYTES,
        headers={**_jwt_admin_headers(), "Content-Type": "image/webp"},
    )
    assert res.status_code == 200
    assert COVER_PUT_CALLS == [("channels/chan-1/cover.webp", WEBP_BYTES, "image/webp")]


def test_upload_cover_declared_png_but_text_body_returns_400(client: TestClient) -> None:
    _seed_channel(id="chan-1")

    res = client.post(
        "/admin/channels/chan-1/cover",
        content=b"this is just plain text, not a png",
        headers={**_jwt_admin_headers(), "Content-Type": "image/png"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert COVER_PUT_CALLS == []


def test_upload_cover_svg_returns_400(client: TestClient) -> None:
    _seed_channel(id="chan-1")

    res = client.post(
        "/admin/channels/chan-1/cover",
        content=b"<svg><script>alert(1)</script></svg>",
        headers={**_jwt_admin_headers(), "Content-Type": "image/svg+xml"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert COVER_PUT_CALLS == []


def test_upload_cover_empty_body_returns_400(client: TestClient) -> None:
    _seed_channel(id="chan-1")

    res = client.post(
        "/admin/channels/chan-1/cover",
        content=b"",
        headers={**_jwt_admin_headers(), "Content-Type": "image/png"},
    )
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert COVER_PUT_CALLS == []


def test_upload_cover_over_size_limit_returns_413(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_channel(id="chan-1")
    monkeypatch.setattr(
        admin_router,
        "get_settings",
        lambda: Settings(environment="dev", admin_email=ADMIN_EMAIL, channel_cover_max_bytes=10),
    )

    res = client.post(
        "/admin/channels/chan-1/cover",
        content=PNG_BYTES,  # 24 bytes > 10 bytes 上限
        headers={**_jwt_admin_headers(), "Content-Type": "image/png"},
    )
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "payload_too_large"
    assert COVER_PUT_CALLS == []


def test_upload_cover_channel_not_found_returns_404(client: TestClient) -> None:
    res = client.post(
        "/admin/channels/does-not-exist/cover",
        content=PNG_BYTES,
        headers={**_jwt_admin_headers(), "Content-Type": "image/png"},
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert COVER_PUT_CALLS == []
