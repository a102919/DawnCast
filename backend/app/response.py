"""ApiResponse 泛型 envelope：所有 endpoint 一律回 {ok, data} / {ok, error}。

禁裸回傳資料（coding-rules）。前端 httpApi 解包約定見模組尾註。
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorBody(BaseModel):
    """對外錯誤只含 code/message，不洩漏 stack trace / SQL / 內部路徑。"""

    code: str
    message: str


class ApiResponse[T](BaseModel):
    """統一回應信封。

    成功：{"ok": true, "data": <T>, "error": null}
    失敗：{"ok": false, "data": null, "error": {"code", "message"}}
    """

    ok: bool
    data: T | None = None
    error: ErrorBody | None = None


def ok[T](data: T) -> ApiResponse[T]:
    """成功回應。data 可為任何 pydantic 模型 / 基本型別 / None。"""
    return ApiResponse[T](ok=True, data=data)


def err(code: str, message: str) -> ApiResponse[None]:
    """錯誤回應。供 exception handler 使用。"""
    return ApiResponse[None](ok=False, error=ErrorBody(code=code, message=message))


# ── 前端 httpApi（Phase 4）解包約定 ───────────────────────────────
#
# 後端 endpoint 一律回 {ok, data, error}。前端 httpApi.ts 應：
#   const env = await res.json()
#   if (!env.ok) throw new AppError(env.error.code, env.error.message)
#   return env.data            // 解出的 data 形狀 === mockApi 既有回傳
#
# 即：data 的形狀與 mockApi 各方法回傳值「逐欄位一致」（camelCase，
# 由 shared.models CamelModel + model_dump(by_alias=True) 保證）。
# 回傳「無內容」的方法（removeVocab/clearVocab/addFavorite…）data 為 null。
