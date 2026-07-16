"""config.assert_secure 上線防呆測試（MED-1）。

dev 不檢查；prod 對「預設 JWT secret / 空 secret / CORS '*'」一律 fail closed。
"""

from __future__ import annotations

import pytest

from shared.config import Settings
from shared.errors import ConfigError


def test_dev_allows_default_secret() -> None:
    """dev 環境用預設值可正常啟動（本機 / 測試免設定）。"""
    Settings(environment="dev").assert_secure()  # 不該 raise


def test_prod_rejects_default_jwt_secret() -> None:
    with pytest.raises(ConfigError):
        Settings(environment="prod").assert_secure()


def test_prod_rejects_empty_jwt_secret() -> None:
    with pytest.raises(ConfigError):
        Settings(environment="prod", supabase_jwt_secret="").assert_secure()


def test_prod_rejects_wildcard_cors() -> None:
    with pytest.raises(ConfigError):
        Settings(
            environment="prod",
            supabase_jwt_secret="a-real-secret",
            cors_allowed_origins=["*"],
        ).assert_secure()


def test_prod_accepts_secure_config() -> None:
    Settings(
        environment="prod",
        supabase_jwt_secret="a-real-secret",
        cors_allowed_origins=["https://dawncast.app"],
        admin_token="a-real-admin-token",
    ).assert_secure()  # 不該 raise


def test_prod_rejects_empty_admin_token() -> None:
    with pytest.raises(ConfigError):
        Settings(
            environment="prod",
            supabase_jwt_secret="a-real-secret",
            cors_allowed_origins=["https://dawncast.app"],
            admin_token="",
        ).assert_secure()


def test_prod_accepts_admin_token_set() -> None:
    Settings(
        environment="prod",
        supabase_jwt_secret="a-real-secret",
        cors_allowed_origins=["https://dawncast.app"],
        admin_token="a-real-admin-token",
    ).assert_secure()  # 不該 raise
