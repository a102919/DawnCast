"""依賴注入：驗 Supabase JWT，取出 user_id。

授權主防線在此：每個受保護 endpoint 都依賴 get_current_user，
查詢一律以回傳的 user_id 收斂（server 端授權，不信任前端）。
"""

from __future__ import annotations

import logging

from fastapi import Header
from jose import JWTError, jwt  # type: ignore[import-untyped]  # python-jose 無 stubs

from shared.config import get_settings
from shared.errors import AuthError

logger = logging.getLogger(__name__)

UserId = str


def _decode(token: str) -> str:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience=settings.supabase_jwt_audience,
        )
    except JWTError as exc:
        # 詳細只寫 log，對外只回 generic（不洩漏為何失敗）
        logger.info("JWT 驗證失敗: %s", exc)
        raise AuthError("認證失敗") from exc

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("認證失敗")
    return sub


async def get_current_user(authorization: str | None = Header(default=None)) -> UserId:
    """從 Authorization: Bearer <jwt> 取出並驗證 user_id（sub）。

    dev bypass：environment=dev 且 dev_auth_bypass=true，且 Authorization 是
    'Bearer dev' 或缺 → 直接回 dev_user_id。本機預覽不繞 Supabase。
    prod 強制走 JWT（assert_secure 已擋預設 secret 啟動）。
    """
    settings = get_settings()
    if (
        settings.environment == "dev"
        and settings.dev_auth_bypass
        and settings.dev_user_id
        and (authorization is None or authorization.lower() == "bearer dev")
    ):
        return settings.dev_user_id
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("缺少授權標頭")
    token = authorization[7:].strip()
    if not token:
        raise AuthError("缺少授權標頭")
    return _decode(token)
