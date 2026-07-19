"""FastAPI app：lifespan 管 pool、註冊 routers、AppError → ApiResponse handler。

對外錯誤只回 {code, message}（不洩漏 stack trace / SQL / 內部路徑）；
未預期錯誤回 generic 500，詳細只寫 log。CORS 允許前端 dev origin。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.middleware import RateLimitMiddleware
from app.response import err
from app.routers import (
    account,
    activity,
    admin,
    daily_orders,
    episodes,
    favorites,
    jobs,
    notifications,
    vocab,
)
from app.routers import (
    dict as dict_router,
)
from app.routers import (
    settings as settings_router,
)
from shared.config import get_settings
from shared.db.pool import close_pool, open_pool
from shared.errors import AppError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 上線防呆：prod 設定不安全（預設 JWT secret / CORS '*'）直接拒絕啟動。
    get_settings().assert_secure()
    await open_pool()
    try:
        yield
    finally:
        await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="DawnCast API", lifespan=lifespan)

    settings = get_settings()

    # /dict/lookup 限流（per-IP sliding window，in-memory）；擋 LLM fallback 撞量。
    # 順序關鍵：RateLimit 先加（內層），CORS 後加（最外層）—— 讓 429 JSONResponse
    # 回程仍會被 CORSMiddleware 包到、補上 Access-Control-Allow-Origin，
    # 跨域 SPA 才能在瀏覽器端讀到 rate_limited envelope（缺 ACAO 會被預檢擋）。
    app.add_middleware(
        RateLimitMiddleware,
        limit=settings.rate_limit_dict_per_min,
        window_sec=60.0,
    )

    # CORS kwargs：env-aware。dev 才啟用 PNA + origin regex（opt-in 相容路徑，
    # 給不走 vite proxy、直接打後端的場景留後路；prod 完全不開，fail-secure）。
    cors_kwargs: dict[str, object] = {
        "allow_origins": settings.cors_allowed_origins,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if settings.environment == "dev":
        # Private Network Access (PNA / CORS-RFC1918)：HTTPS 公開來源（e.g. devtunnels）
        # 打本機 loopback 必須明確同意，否則 Chrome 直接擋。
        cors_kwargs["allow_private_network"] = True
        if settings.cors_allowed_origin_regex:
            cors_kwargs["allow_origin_regex"] = settings.cors_allowed_origin_regex
    app.add_middleware(CORSMiddleware, **cors_kwargs)

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        # 4xx 屬預期；message 已是對外安全字串
        body = err(exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # 邊界驗證失敗回 400，詳細只寫 log（不回 pydantic 內部結構給前端）
        logger.info("請求驗證失敗: %s", exc.errors())
        body = err("validation_error", "請求參數不正確")
        return JSONResponse(status_code=400, content=body.model_dump())

    @app.exception_handler(Exception)
    async def unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        # 未預期錯誤：詳細只寫 log，對外 generic 500（不洩漏內部）
        logger.exception("未預期錯誤: %s", exc)
        body = err("internal_error", "伺服器發生錯誤")
        return JSONResponse(status_code=500, content=body.model_dump())

    app.include_router(vocab.router)
    app.include_router(settings_router.router)
    app.include_router(favorites.router)
    app.include_router(daily_orders.router)
    app.include_router(episodes.router)
    app.include_router(dict_router.router)
    app.include_router(activity.router)
    app.include_router(account.router)  # T4：帳號自我管理（URL 字面 /me）
    app.include_router(admin.router)
    app.include_router(jobs.router)
    app.include_router(notifications.router)

    # 本機 fallback：當 R2 未設且 LOCAL_MEDIA_DIR 指向本地資料夾時，
    # 把整個目錄掛到 /media/* 讓前端視訊標籤能直接 src=。
    # 沒設 → 不掛（prod 預期走 R2 presign，本機才需要）。
    from pathlib import Path

    media_dir = settings.local_media_dir
    if media_dir and Path(media_dir).is_dir():
        app.mount("/media", StaticFiles(directory=media_dir), name="media")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
