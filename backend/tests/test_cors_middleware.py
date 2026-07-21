"""CORS middleware 行為測試。

驗證 dev / prod 在 Private Network Access (PNA) 與 origin regex 上的差異：
  - dev 帶 PNA preflight（Access-Control-Request-Private-Network: true）
    → 200 + ACA-Private-Network: true
  - dev 帶 origin 命中 devtunnels regex → 200 + ACA-Origin 回填該 origin
  - prod 帶同樣 PNA preflight → 400 + **不帶** ACA-Private-Network
  - prod origin regex 不啟用 → 非 cors_allowed_origins 內的 origin 400
  - prod 精確 origin → 200 + ACA-Origin

模式：參考 test_dict_rate_limit.py 的 make_settings / make_app fixture，
patch app.main.get_settings 後 create_app() 讀到新設定。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from shared.config import Settings, get_settings

DEV_ORIGIN_LOCAL = "http://localhost:5173"
DEV_ORIGIN_TUNNEL = "https://abc12345-5173.jpe1.devtunnels.ms"
PROD_ORIGIN = "https://dawncast.app"


def _patched_get_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.get_settings", lambda: settings)


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch):
    """工廠：給定 Settings 環境，回傳獨立 TestClient。"""

    def _make(environment: str, **overrides: Any) -> TestClient:
        get_settings.cache_clear()
        base = get_settings()
        new_settings = base.model_copy(
            update={"environment": environment, **overrides}
        )
        _patched_get_settings(monkeypatch, new_settings)
        from app.main import create_app

        return TestClient(
            create_app(), raise_server_exceptions=False
        )

    return _make


# ── dev：PNA + devtunnels regex 啟用 ──────────────────────────────────


def test_dev_preflight_returns_pna_header_for_local_origin(
    make_client: Any,
) -> None:
    """dev 帶 PNA preflight + localhost origin → 200 + ACA-Private-Network。"""
    client = make_client("dev", cors_allowed_origin_regex="")
    r = client.options(
        "/vocab",
        headers={
            "Origin": DEV_ORIGIN_LOCAL,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-private-network") == "true"
    assert r.headers.get("access-control-allow-origin") == DEV_ORIGIN_LOCAL


def test_dev_preflight_allows_devtunnel_origin_via_regex(
    make_client: Any,
) -> None:
    """dev 帶 PNA preflight + devtunnels 子網域（regex 命中）→ 200 + ACA-Origin 回填。"""
    client = make_client(
        "dev",
        cors_allowed_origin_regex=r"https://[a-z0-9-]+\.jpe1\.devtunnels\.ms",
    )
    r = client.options(
        "/vocab",
        headers={
            "Origin": DEV_ORIGIN_TUNNEL,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-private-network") == "true"
    assert r.headers.get("access-control-allow-origin") == DEV_ORIGIN_TUNNEL


def test_dev_rejects_origin_outside_whitelist_and_regex(
    make_client: Any,
) -> None:
    """dev 帶未授權 origin（既不在 list 也不在 regex）→ 400。"""
    client = make_client("dev", cors_allowed_origin_regex="")
    r = client.options(
        "/vocab",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 400


# ── prod：PNA + regex 完全不啟用，fail-secure ──────────────────────────


def test_prod_preflight_omits_pna_header(make_client: Any) -> None:
    """prod 即使收到 PNA preflight → 400 且**不帶** ACA-Private-Network。"""
    client = make_client("prod", cors_allowed_origins=[PROD_ORIGIN])
    r = client.options(
        "/vocab",
        headers={
            "Origin": PROD_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert r.status_code == 400
    assert "access-control-allow-private-network" not in {
        k.lower() for k in r.headers
    }


def test_prod_allows_exact_origin_in_whitelist(make_client: Any) -> None:
    """prod 帶白名單內 origin → 200。"""
    client = make_client("prod", cors_allowed_origins=[PROD_ORIGIN])
    r = client.options(
        "/vocab",
        headers={
            "Origin": PROD_ORIGIN,
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == PROD_ORIGIN


def test_prod_ignores_origin_regex_even_when_set(make_client: Any) -> None:
    """prod 即便誤帶 cors_allowed_origin_regex（assert_secure 會擋），middleware 也跳過。

    這是雙重保險：assert_secure 擋啟動、middleware 擋 runtime。
    """
    client = make_client(
        "prod",
        cors_allowed_origins=[PROD_ORIGIN],
        # assert_secure 不跑（測試直接 create_app），模擬誤帶狀況。
        cors_allowed_origin_regex=r"https://.*\.devtunnels\.ms",
    )
    r = client.options(
        "/vocab",
        headers={
            "Origin": "https://abc12345-5173.jpe1.devtunnels.ms",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 400
