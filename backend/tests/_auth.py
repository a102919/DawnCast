"""測試專用 auth helper：取代舊版 HS256 + supabase_jwt_secret 簽 token 邏輯。

backend 改成 ES256 + JWKS 驗 token 之後，測試要：
  1. 用 ECC P-256 私鑰簽 ES256 JWT（mock 真實 Supabase 簽章）
  2. 把對應的 JWK 公開 key 注入 deps._jwks_factory（mock JWKS endpoint）
  3. JWT header 帶 kid，模擬 Supabase 真實 token 結構

典型用法：

    from tests._auth import sign_test_token, auth_header

    def test_x(client):
        res = client.get("/me", headers=auth_header("user-uuid"))
        assert res.status_code == 200

首次呼叫 sign_test_token() / auth_header() 會自動注入 monkeypatch，
之後所有測試共享同一個 key pair。
"""

from __future__ import annotations

import threading
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt
from jose.utils import long_to_base64

from app import deps
from shared.config import get_settings

_KID = "test-es256-key-1"
_ALG = "ES256"

# module-level singleton：所有測試共享同一個 key pair（單 process 內 JWKS 只有一份）
_init_lock = threading.Lock()
_priv: ec.EllipticCurvePrivateKey | None = None
_priv_pem: bytes | None = None
_jwks: dict[str, Any] | None = None


def _ensure_init() -> None:
    global _priv, _priv_pem, _jwks
    with _init_lock:
        if _priv is not None:
            return
        _priv = ec.generate_private_key(ec.SECP256R1())
        _priv_pem = _priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        numbers = _priv.public_key().public_numbers()
        _jwks = {
            "keys": [
                {
                    "kty": "EC",
                    "crv": "P-256",
                    "use": "sig",
                    "alg": _ALG,
                    "kid": _KID,
                    "x": long_to_base64(numbers.x).decode("ascii"),
                    "y": long_to_base64(numbers.y).decode("ascii"),
                }
            ]
        }
        # 注入 fake JWKS factory + 清掉舊 cache（避免先前測試的 cache 殘留）
        deps._jwks_factory = lambda _settings: _jwks
        deps._invalidate_jwks_cache()


def sign_test_token(user_id: str, **extra: Any) -> str:
    """簽一支 mock Supabase token：sub=user_id, aud=authenticated, header.kid=test key。"""
    _ensure_init()
    assert _priv_pem is not None
    settings = get_settings()
    payload: dict[str, Any] = {"sub": user_id, "aud": settings.supabase_jwt_audience, **extra}
    return str(jwt.encode(payload, _priv_pem, algorithm=_ALG, headers={"kid": _KID}))


def auth_header(user_id: str, **extra: Any) -> dict[str, str]:
    """回傳 {'Authorization': 'Bearer <token>'}，給 TestClient 直接用。"""
    return {"Authorization": f"Bearer {sign_test_token(user_id, **extra)}"}


def reset_jwks_factory() -> None:
    """解除 monkeypatch（給少數不想用 fake JWKS 的測試用）。"""
    deps._jwks_factory = None
    deps._invalidate_jwks_cache()
