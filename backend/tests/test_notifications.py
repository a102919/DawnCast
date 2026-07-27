"""Push 通知測試：router 授權/契約 + shared/push.py 的失效訂閱清理。

驗證重點：
  (a) subscribe / unsubscribe 無 JWT → 401，且完全不動資料
  (b) happy path：upsert 冪等、unsubscribe 找不到列也回成功
  (c) 授權收斂：unsubscribe 只能刪自己的 endpoint（router 漏 user_id 就會踩到 B 的）
  (d) notify_user 遇 410/404 刪掉該筆訂閱；5xx 保留（一次抖動不該永久掉訂閱）
  (e) 沒設 VAPID key 時 notify_user 直接 no-op，連 DB 都不碰

做法：router 與 push 都走 shared.db.repo，直接 monkeypatch repo 的函式，
不重造 FakeConnection（比 test_favorites_router.py 的 fake cursor 更薄）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from shared import push as push_mod
from shared.config import Settings
from shared.db import repo

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"

EP_A = "https://push.example/a"
EP_B = "https://push.example/b"

# (user_id, endpoint, p256dh, auth)
SUBS: list[tuple[str, str, str, str]] = []


async def _fake_upsert(user_id: str, endpoint: str, p256dh: str, auth: str) -> None:
    SUBS[:] = [s for s in SUBS if s[1] != endpoint]
    SUBS.append((user_id, endpoint, p256dh, auth))


async def _fake_delete_for_user(user_id: str, endpoint: str) -> None:
    SUBS[:] = [s for s in SUBS if not (s[0] == user_id and s[1] == endpoint)]


async def _fake_delete_endpoints(endpoints: list[str]) -> None:
    SUBS[:] = [s for s in SUBS if s[1] not in endpoints]


async def _fake_list(user_id: str) -> list[dict[str, str]]:
    return [{"endpoint": e, "p256dh": p, "auth": a} for u, e, p, a in SUBS if u == user_id]


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "environment": "dev",
        "app_timezone": "Asia/Taipei",
        "vapid_public_key": "BPublicKeyForTestOnly",
        "vapid_private_key": "PrivateKeyForTestOnly",
        "vapid_subject": "mailto:test@example.com",
    }
    return Settings(**{**base, **over})


@pytest.fixture(autouse=True)
def patch_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    SUBS[:] = [
        (USER_A, EP_A, "p-a", "auth-a"),
        (USER_B, EP_B, "p-b", "auth-b"),
    ]
    monkeypatch.setattr(push_mod, "get_settings", _settings)
    monkeypatch.setattr(repo, "upsert_push_subscription", _fake_upsert)
    monkeypatch.setattr(repo, "delete_push_subscription", _fake_delete_for_user)
    monkeypatch.setattr(repo, "list_push_subscriptions", _fake_list)
    monkeypatch.setattr(repo, "delete_push_endpoints", _fake_delete_endpoints)


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def _auth(user_id: str) -> dict[str, str]:
    from tests._auth import sign_test_token

    return {"Authorization": f"Bearer {sign_test_token(user_id)}"}


_NEW_SUB: dict[str, Any] = {
    "endpoint": "https://push.example/new",
    "keys": {"p256dh": "p-new", "auth": "auth-new"},
}


# ── (a) 無 JWT → 401 ──────────────────────────────────────────


def test_subscribe_no_jwt_returns_401_and_writes_nothing(client: TestClient) -> None:
    before = list(SUBS)
    res = client.post("/notifications/subscription", json=_NEW_SUB)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "unauthorized"
    assert before == SUBS


def test_unsubscribe_no_jwt_returns_401_and_deletes_nothing(client: TestClient) -> None:
    before = list(SUBS)
    res = client.request("DELETE", "/notifications/subscription", json={"endpoint": EP_A})
    assert res.status_code == 401
    assert before == SUBS


# ── (b) happy path ───────────────────────────────────────────


def test_subscribe_stores_subscription(client: TestClient) -> None:
    res = client.post("/notifications/subscription", json=_NEW_SUB, headers=_auth(USER_A))
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert (USER_A, "https://push.example/new", "p-new", "auth-new") in SUBS


def test_subscribe_is_idempotent(client: TestClient) -> None:
    for _ in range(2):
        res = client.post("/notifications/subscription", json=_NEW_SUB, headers=_auth(USER_A))
        assert res.status_code == 200
    assert sum(1 for _, e, _, _ in SUBS if e == "https://push.example/new") == 1


def test_unsubscribe_removes_own_subscription(client: TestClient) -> None:
    res = client.request(
        "DELETE", "/notifications/subscription", json={"endpoint": EP_A}, headers=_auth(USER_A)
    )
    assert res.status_code == 200
    assert all(e != EP_A for _, e, _, _ in SUBS)


def test_unsubscribe_unknown_endpoint_still_ok(client: TestClient) -> None:
    res = client.request(
        "DELETE",
        "/notifications/subscription",
        json={"endpoint": "https://push.example/nope"},
        headers=_auth(USER_A),
    )
    assert res.status_code == 200


def test_subscribe_rejects_non_https_endpoint(client: TestClient) -> None:
    res = client.post(
        "/notifications/subscription",
        json={**_NEW_SUB, "endpoint": "http://push.example/x"},
        headers=_auth(USER_A),
    )
    # app 的 validation handler 把 Pydantic 422 統一轉成 400（見 app/main.py）
    assert res.status_code == 400


# ── (c) 授權收斂 ──────────────────────────────────────────────


def test_unsubscribe_cannot_delete_other_users_endpoint(client: TestClient) -> None:
    res = client.request(
        "DELETE", "/notifications/subscription", json={"endpoint": EP_B}, headers=_auth(USER_A)
    )
    assert res.status_code == 200
    # B 的訂閱必須還在——router 若漏掉 user_id 條件，這行會炸。
    assert (USER_B, EP_B, "p-b", "auth-b") in SUBS


# ── (d)(e) notify_user ───────────────────────────────────────


class _FakeWebPushException(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"status={status}")

        class _Resp:
            status_code = status

        self.response = _Resp()


def _patch_webpush_failure(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setattr(push_mod, "WebPushException", _FakeWebPushException)

    def _boom(**_: Any) -> None:
        raise _FakeWebPushException(status)

    monkeypatch.setattr(push_mod, "webpush", _boom)


async def test_notify_user_sends_to_all_own_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    def _record(**kwargs: Any) -> None:
        sent.append(kwargs["subscription_info"]["endpoint"])

    monkeypatch.setattr(push_mod, "webpush", _record)
    n = await push_mod.notify_user(USER_A, {"title": "t", "body": "b", "url": "/"})
    assert n == 1
    assert sent == [EP_A]


async def test_notify_user_prunes_gone_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_webpush_failure(monkeypatch, 410)
    assert await push_mod.notify_user(USER_A, {"title": "t", "body": "b", "url": "/"}) == 0
    assert all(e != EP_A for _, e, _, _ in SUBS)
    # 別人的訂閱不受影響
    assert any(u == USER_B for u, _, _, _ in SUBS)


async def test_notify_user_keeps_subscription_on_transient_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_webpush_failure(monkeypatch, 500)
    assert await push_mod.notify_user(USER_A, {"title": "t", "body": "b", "url": "/"}) == 0
    # 500 是暫時性錯誤，訂閱要留著（否則一次 provider 抖動就永久掉訂閱）
    assert any(e == EP_A for _, e, _, _ in SUBS)


async def test_notify_user_noop_without_vapid_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        push_mod, "get_settings", lambda: _settings(vapid_public_key="", vapid_private_key="")
    )

    def _should_not_run(**_: Any) -> None:
        raise AssertionError("沒設 VAPID key 時不該送出推播")

    async def _should_not_query(_user_id: str) -> list[dict[str, str]]:
        raise AssertionError("沒設 VAPID key 時不該查 DB")

    monkeypatch.setattr(push_mod, "webpush", _should_not_run)
    monkeypatch.setattr(repo, "list_push_subscriptions", _should_not_query)
    assert await push_mod.notify_user(USER_A, {"title": "t", "body": "b", "url": "/"}) == 0
