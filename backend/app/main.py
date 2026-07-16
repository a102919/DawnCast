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

from app.response import err
from app.routers import (
    activity,
    admin,
    daily_orders,
    episodes,
    favorites,
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    app.include_router(admin.router)
    app.include_router(notifications.router)

    # 本機 fallback：當 R2 未設且 LOCAL_MEDIA_DIR 指向本地資料夾時，
    # 把整個目錄掛到 /media/* 讓前端視訊標籤能直接 src=。
    # 沒設 → 不掛（prod 預期走 R2 presign，本機才需要）。
    from pathlib import Path

    settings = get_settings()
    media_dir = settings.local_media_dir
    if media_dir and Path(media_dir).is_dir():
        app.mount("/media", StaticFiles(directory=media_dir), name="media")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
