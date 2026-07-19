"""config.assert_secure 上線防呆測試（MED-1）。

dev 不檢查；prod 對「預設 JWKS URL / 空 JWKS URL / CORS '*'」一律 fail closed。
"""

from __future__ import annotations

import pytest

from shared.config import Settings
from shared.errors import ConfigError


def test_dev_allows_defaults() -> None:
    """dev 環境用預設值可正常啟動（本機 / 測試免設定）。"""
    Settings(environment="dev").assert_secure()  # 不該 raise


def test_prod_rejects_default_jwks_url() -> None:
    with pytest.raises(ConfigError):
        Settings(environment="prod", supabase_jwks_url="").assert_secure()


def test_prod_rejects_wildcard_cors() -> None:
    with pytest.raises(ConfigError):
        Settings(
            environment="prod",
            supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            cors_allowed_origins=["*"],
        ).assert_secure()


def test_prod_accepts_secure_config() -> None:
    Settings(
        environment="prod",
        supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        cors_allowed_origins=["https://dawncast.app"],
        cors_allowed_origin_regex="",
        admin_token="a-real-admin-token",
    ).assert_secure()  # 不該 raise


def test_prod_rejects_empty_admin_token() -> None:
    with pytest.raises(ConfigError):
        Settings(
            environment="prod",
            supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            cors_allowed_origins=["https://dawncast.app"],
            cors_allowed_origin_regex="",
            admin_token="",
        ).assert_secure()


def test_prod_accepts_admin_token_set() -> None:
    Settings(
        environment="prod",
        supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        cors_allowed_origins=["https://dawncast.app"],
        cors_allowed_origin_regex="",
        admin_token="a-real-admin-token",
    ).assert_secure()  # 不該 raise


def test_prod_rejects_nonempty_cors_origin_regex() -> None:
    """prod 帶到 devtunnels regex = 放行任意子網域，視為 fail。"""
    with pytest.raises(ConfigError, match="CORS_ALLOWED_ORIGIN_REGEX"):
        Settings(
            environment="prod",
            supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            cors_allowed_origins=["https://dawncast.app"],
            cors_allowed_origin_regex="https://[a-z0-9-]+\\.devtunnels\\.ms",
            admin_token="x",
        ).assert_secure()


def test_prod_treats_whitespace_only_cors_origin_regex_as_unset() -> None:
    """純空白視同未設定（regex.strip() 為空）。dotenv 偶爾會帶到行尾空白。"""
    Settings(
        environment="prod",
        supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        cors_allowed_origins=["https://dawncast.app"],
        cors_allowed_origin_regex="   ",
        admin_token="x",
    ).assert_secure()  # 不該 raise


def test_prod_accepts_empty_cors_origin_regex() -> None:
    Settings(
        environment="prod",
        supabase_jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        cors_allowed_origins=["https://dawncast.app"],
        cors_allowed_origin_regex="",
        admin_token="x",
    ).assert_secure()  # 不該 raise


def test_dev_accepts_nonempty_cors_origin_regex() -> None:
    """dev 帶 regex 不該 fail——opt-in 相容路徑（不走 vite proxy 直接打後端）。"""
    Settings(
        environment="dev",
        cors_allowed_origin_regex="https://[a-z0-9-]+\\.devtunnels\\.ms",
    ).assert_secure()  # 不該 raise
