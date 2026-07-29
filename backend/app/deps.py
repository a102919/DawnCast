"""依賴注入：驗 Supabase JWT（ES256，公開 JWKS），取出 user_id。

Supabase 已於 2025 年把預設 JWT 簽章從 HS256（對稱）換成 ES256（ECC P-256）。
後端不再持有 secret，改抓 JWKS 拿公開 key 做驗簽 — 業界標準、支援自動輪換。

授權主防線在此：每個受保護 endpoint 都依賴 get_current_user，
查詢一律以回傳的 user_id 收斂（server 端授權，不信任前端）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
from fastapi import Header
from jose import jwt
from jose.exceptions import JWTError

from shared.config import Settings, get_settings
from shared.errors import AuthError

logger = logging.getLogger(__name__)

UserId = str

# JWKS cache：避免每個 request 都打 Supabase。
# Key rotation 期間 Supabase 會回 'kid not found'，屆時 invalidate cache 重抓一次。
_JWKS_TTL_SEC = 3600
# 強制重抓冷卻：未認證亂 kid 會把 cache 失效、若無 cooldown 等於每個請求打一次
# Supabase JWKS endpoint，可把整個服務登入流程連坐打掛。冷卻期間直接 401，
# 不打外網（rotation 期間真實使用者多撐 60 秒也只影響一次登入，可接受）。
_JWKS_REFETCH_COOLDOWN_SEC = 60
_jwks_lock = threading.Lock()
_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0
_jwks_last_forced_refetch: float = 0.0

# 測試可 monkeypatch 這個 factory 注入 fake JWKS；prod 一律走 _fetch_jwks_http。
_JwksFactory = Callable[[Settings], dict[str, Any]]
_jwks_factory: _JwksFactory | None = None


def _fetch_jwks_http(url: str) -> dict[str, Any]:
    """從 Supabase JWKS endpoint 抓公開 key set。"""
    res = httpx.get(url, timeout=5.0)
    res.raise_for_status()
    payload: dict[str, Any] = res.json()
    return payload


def _get_jwks(settings: Settings) -> dict[str, Any]:
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    with _jwks_lock:
        if _jwks_cache is not None and (now - _jwks_fetched_at) < _JWKS_TTL_SEC:
            return _jwks_cache
        payload = (
            _jwks_factory(settings)
            if _jwks_factory
            else _fetch_jwks_http(settings.supabase_jwks_url)
        )
        _jwks_cache = payload
        _jwks_fetched_at = now
        return payload


def _invalidate_jwks_cache() -> None:
    """kid 找不到時呼叫：可能 Supabase 已輪換，重抓一次。"""
    global _jwks_cache, _jwks_fetched_at
    with _jwks_lock:
        _jwks_cache = None
        _jwks_fetched_at = 0.0


def _force_refetch_jwks() -> bool:
    """強制 invalidate + 重抓 JWKS，回傳 True = 在冷卻期內已跳過。

    包成一個 step：callers 不需自己 invalidate 再 _get_jwks，避免兩段流程
    各自觸發 fetch 導致計數翻倍。冷卻期間不 invalidate、不重抓，直接 False。
    """
    global _jwks_last_forced_refetch
    now = time.monotonic()
    with _jwks_lock:
        last = _jwks_last_forced_refetch
        if last > 0 and (now - last) < _JWKS_REFETCH_COOLDOWN_SEC:
            return False
        _jwks_last_forced_refetch = now
        _jwks_cache = None
        _jwks_fetched_at = 0.0
    return True


def extract_bearer_token(authorization: str | None) -> str | None:
    """從 Authorization header 取出 Bearer token 字串；格式不符或缺 token 回 None。

    get_current_user 與 account.py 的 _jwt_email 都要先做這一步才能驗證/解碼，
    抽成公開 helper 避免兩處各自重寫一份幾乎一樣的字串解析邏輯。
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    return token or None


def _decode(token: str) -> str:
    payload = decode_jwt_payload(token)
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("認證失敗")
    return sub


def decode_jwt_payload(token: str, *, require_exp: bool = False) -> dict[str, Any]:
    """驗 JWT，回傳完整 payload。給 account.py 的 _jwt_email 等需要額外 claim 的場景用。

    兩個 mode：
    - ES256（default，cloud Supabase）：從 JWKS 拿公鑰 verify。
    - HS256（self-host opt-in）：用 SUPABASE_JWT_SECRET 直接 verify。
      路徑在 SUPABASE_JWT_ALG=HS256 時啟用；prod 仍要求 secret 非預設（見
      Settings.assert_secure）。

    require_exp=True：強制 token 必須帶 exp claim（python-jose 預設只驗 exp 合法
    性，不要求存在）；admin 等敏感入口用，免費防禦長效 token。
    """
    settings = get_settings()

    options = {"require_exp": require_exp} if require_exp else None

    if settings.supabase_jwt_alg == "HS256":
        try:
            payload = jwt.decode(
                token,
                settings.supabase_jwt_secret,
                algorithms=["HS256"],
                audience=settings.supabase_jwt_audience,
                options=options,
            )
        except JWTError as exc:
            logger.info("JWT 驗證失敗（HS256）: %s", exc)
            raise AuthError("認證失敗") from exc
        if not isinstance(payload, dict):
            raise AuthError("認證失敗")
        return payload

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        logger.info("JWT header 解析失敗: %s", exc)
        raise AuthError("認證失敗") from exc

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise AuthError("認證失敗")

    try:
        jwks = _get_jwks(settings)
        key = _find_key(jwks, kid)
        if key is None:
            # 沒找到 → 強制重抓，cover key rotation 邊界；
            # 但加冷卻避免未認證亂 kid 把整個服務打掛（共享 module-level cache，
            # 會連坐把 get_current_user 一起拖累）。冷卻期間直接 401。
            if not _force_refetch_jwks():
                raise AuthError("認證失敗")
            jwks = _get_jwks(settings)
            key = _find_key(jwks, kid)
    except Exception as exc:
        logger.info("JWKS 取得失敗: %s", exc)
        raise AuthError("認證失敗") from exc

    if key is None:
        raise AuthError("認證失敗")

    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=["ES256"],
            audience=settings.supabase_jwt_audience,
            options=options,
        )
    except JWTError as exc:
        logger.info("JWT 驗證失敗: %s", exc)
        raise AuthError("認證失敗") from exc

    if not isinstance(payload, dict):
        raise AuthError("認證失敗")
    return payload


def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for k in jwks.get("keys", []):
        if isinstance(k, dict) and k.get("kid") == kid:
            return k
    return None


def is_dev_bypass(authorization: str | None) -> bool:
    """判斷是否符合 dev bypass 條件：environment=dev 且 dev_auth_bypass=true，
    且 Authorization 是 'Bearer dev' 或缺。

    get_current_user 與 account.py 的 _jwt_email 共用此判斷，避免安全邏輯複製兩份。
    """
    settings = get_settings()
    return (
        settings.environment == "dev"
        and settings.dev_auth_bypass
        and bool(settings.dev_user_id)
        and (authorization is None or authorization.lower() == "bearer dev")
    )


async def get_current_user(authorization: str | None = Header(default=None)) -> UserId:
    """從 Authorization: Bearer <jwt> 取出並驗證 user_id（sub）。

    dev bypass：environment=dev 且 dev_auth_bypass=true，且 Authorization 是
    'Bearer dev' 或缺 → 直接回 dev_user_id。本機預覽不繞 Supabase。
    prod 強制走 ES256 + JWKS（assert_secure 已擋預設 JWKS URL）。
    """
    if is_dev_bypass(authorization):
        return get_settings().dev_user_id
    token = extract_bearer_token(authorization)
    if not token:
        raise AuthError("缺少授權標頭")
    return _decode(token)
