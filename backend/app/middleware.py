"""Rate-limit middleware（in-memory sliding window per client IP）。

只對 /dict/lookup 生效，擋 LLM fallback 撞量。每個 client 一個 sliding window：
  1. request 進來時取單調時鐘 now()
  2. 從左側丟掉 now - window_sec 之前的舊紀錄
  3. 若 len(deque) >= limit → 回 429 + ApiResponse envelope
  4. 否則 append(now)，放行

設計取捨（YAGNI）：
  - **in-memory 單 process**：多 worker 部署下實際上限 = N × 設定值。
    spec 明說不引 Redis / 外部 rate-limit 套件，本機 + 單 worker 部署完全夠用。
  - **per-IP 而非 per-user_id**：避免在 middleware 重複解 JWT（已有 app.deps.get_current_user）。
    若未來要 per-user，在 lookup_dict endpoint 加 Depends 層即可，middleware 程式碼 0 改動。
  - **回傳 JSONResponse 而非 raise AppError**：BaseHTTPMiddleware raise 例外時不會走
    @app.exception_handler 鏈（middleware 在 ExceptionMiddleware 之外層），
    直接構造與 AppError handler 相同的 envelope 最簡潔且確定性最高。

時間注入：now() 是 module-level helper，測試 monkeypatch 即可推進時鐘，
不必真的 sleep 60 秒驗證視窗滑動。
"""

from __future__ import annotations

import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.response import err

# 限流路徑（spec：只擋 /dict/lookup）
PROTECTED_PATH = "/dict/lookup"

# 對外錯誤訊息（台灣正體中文）
RATE_LIMIT_MESSAGE = "超過查詞頻率限制"

# 預設視窗：60 秒
DEFAULT_WINDOW_SEC = 60.0


def now() -> float:
    """單調時鐘 helper。

    用 monotonic 而非 perf_counter 是因為 monotonic 對系統時鐘跳動免疫（NTP 校時），
    適合用於「相對時間差」計算。
    """
    return time.monotonic()


class SlidingWindowBucket:
    """Per-key sliding window。

    data: dict[client_key, deque[timestamp]]，timestamp 是 monotonic 浮點秒。
    check(key) 回傳 True 表示放行（已記錄此次 request），
    回傳 False 表示觸發上限（不記錄此次 request）。
    """

    def __init__(self, limit: int, window_sec: float) -> None:
        self._limit = limit
        self._window_sec = window_sec
        self._buckets: dict[str, deque[float]] = {}

    def check(self, client_key: str) -> bool:
        now_ts = now()
        cutoff = now_ts - self._window_sec
        bucket = self._buckets.get(client_key)
        if bucket is None:
            bucket = deque()
            self._buckets[client_key] = bucket
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return False
        bucket.append(now_ts)
        return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """只對 PROTECTED_PATH 套 sliding window；其他路徑透傳。"""

    def __init__(self, app: ASGIApp, *, limit: int, window_sec: float = DEFAULT_WINDOW_SEC) -> None:
        super().__init__(app)
        self._bucket = SlidingWindowBucket(limit=limit, window_sec=window_sec)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path != PROTECTED_PATH:
            return await call_next(request)

        # request.client 在 ASGI lifespan 等非 request 場景可能為 None（dispatch 內實際不會發生）
        client_host = request.client.host if request.client and request.client.host else "unknown"
        if not self._bucket.check(client_host):
            body = err("rate_limited", RATE_LIMIT_MESSAGE)
            return JSONResponse(status_code=429, content=body.model_dump())

        return await call_next(request)