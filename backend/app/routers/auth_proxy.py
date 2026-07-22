"""Reverse proxy：SPA 透過 api-ovate 訪問 gotrue-mon（去掉 /auth/v1/ prefix）。

SPA 用 supabase-js SDK，內部拼 `${SUPABASE_URL}/auth/v1/{path}`。
standalone gotrue v2.x 不認 /auth/v1/ prefix（會 404），所以 api-ovate
透傳到 gotrue-mon:9999。SPA 0 改動、SDK 全功能（signInWithOAuth /
getSession / token refresh / signOut / onAuthStateChange）繼續用。

**不 follow redirect**：gotrue /authorize 回 302 Location: accounts.google.com，
proxy 直接把 Location 透傳給 SPA browser 跟隨跳轉。follow 會把 Google response
吞掉、SPA 拿不到 302。

Zeabur marketplace 內網 service 互通走 HTTP（容器內不通 TLS），TLS 終止
在 Zeabur edge；SPA 對外仍 HTTPS。
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth-proxy"], include_in_schema=False)

# 從 Zeabur marketplace 預設 env 拿 gotrue 內網 host（service-xxx 形式）；
# fallback 寫死避免 env 漏設把 proxy 整個壞掉——這個值跟 marketplace template
# 綁定，理論上不變。
_GOTRUE_INTERNAL_HOST = os.environ.get(
    "GOTRUE_MON_HOST",
    "service-6a5f8db64d439e41ee4d35c5",
)
_GOTRUE_PROXY_TARGET = f"http://{_GOTRUE_INTERNAL_HOST}:9999"

# hop-by-hop headers 不轉發（RFC 7230 §6.1 + 常見實務清單）。
_HOP_BY_HOP = frozenset({
    "host", "content-length", "connection", "keep-alive",
    "transfer-encoding", "upgrade", "expect", "te", "trailer",
})


@router.api_route(
    "/auth/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_to_gotrue(path: str, request: Request) -> Response:
    target = f"{_GOTRUE_PROXY_TARGET}/{path}"
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    body = await request.body()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
    ) as client:
        upstream = await client.request(
            method=request.method,
            url=target,
            params=request.query_params,
            headers=fwd_headers,
            content=body,
            follow_redirects=False,
        )

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    # upstream 可能送 Content-Length 跟實際 body 不符（特別是 302），
    # FastAPI Response 會自己重算，拔掉避免 mismatch。
    resp_headers.pop("content-length", None)
    logger.info("auth_proxy %s %s -> %s", request.method, path, upstream.status_code)
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
    )