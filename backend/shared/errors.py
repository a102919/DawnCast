"""分層 Exception。所有自訂錯誤都帶 code 與 http 狀態碼，禁止 throw 字串。

對外回應只用 code/message（不洩漏 stack trace / SQL / 內部路徑），詳細只寫 log。
"""

from __future__ import annotations


class AppError(Exception):
    """所有應用層錯誤的基底。"""

    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class ValidationError(AppError):
    code = "validation_error"
    status_code = 400


class AuthError(AppError):
    code = "unauthorized"
    status_code = 401


class ForbiddenError(AppError):
    code = "forbidden"
    status_code = 403


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404


class ConflictError(AppError):
    code = "conflict"
    status_code = 409


# ── 引擎 / 批次層（worker 用，不對外）──────────────────────────


class EngineError(AppError):
    code = "engine_error"
    status_code = 502


class GenerationError(EngineError):
    """寫稿失敗：LLM 回應無法解析成合法 ScriptJSON。觸發語意層重試（硬上限）。"""

    code = "generation_failed"


class TTSError(EngineError):
    code = "tts_failed"


class SourceFetchError(EngineError):
    """真實資料來源抓取失敗（news/search/wiki provider）。

    retrieve_sources_node 會捕捉此例外並降級成空 sources，不阻斷生成主流程。
    """

    code = "source_fetch_failed"


class StorageError(EngineError):
    code = "storage_failed"


class RateLimitError(EngineError):
    """主引擎撞限流 / 配額。依 FAILOVER_MODE 決定 degrade 或 failover。"""

    code = "rate_limited"
    status_code = 429


class ConfigError(AppError):
    code = "config_error"
    status_code = 500
