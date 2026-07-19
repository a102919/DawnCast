"""帳號自我管理 router（T4）：GET /me、DELETE /me。

DELETE 觸發 DB FK ON DELETE CASCADE 自動串接清空 8 張 child tables：
deliveries / daily_orders / user_vocab / user_favorites / user_settings /
topic_requests / user_heard_topics / user_activity（schema 在
db/migrations/0001_init.sql + 0009_user_activity.sql）。單條
DELETE FROM public.users WHERE id = %s 即可，無需逐表刪除。

email 從 Supabase JWT payload 解（預設帶 email claim）；其餘欄位
從 public.users 讀。handle_new_user trigger 尚未補列時，GET /me
回 trigger 預設值（tz=Asia/Taipei / delivery_time=07:00）。

注意：auth.users 列未刪（缺 service_role_key + admin SDK，超出 MVP 範圍）。
重新註冊同 email 在應用層級「視為新帳號」成立（public.users 由
handle_new_user trigger 重生、所有 child tables 為空），但 Supabase
auth.users 列保留是已知技術債，下輪再決定是否加 admin SDK。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header
from psycopg.rows import dict_row

from app.deps import _decode_payload, get_current_user
from app.response import ApiResponse, ok
from shared.config import get_settings
from shared.db.pool import connection
from shared.errors import AuthError
from shared.models import AccountInfo

logger = logging.getLogger(__name__)

# 注意：URL 字面 /me（spec 要求）；不採 prefix="/account" 是為了對齊
# RFC 慣例（/me 已是自我資源的業界標準路徑）。
router = APIRouter(tags=["account"])

# public.users 對外 5 欄位（id / tz / delivery_time / created_at）。
# email 不存 public.users → 從 JWT 解。
_SELECT = """
  select id::text as id, tz,
         to_char(delivery_time, 'HH24:MI') as delivery_time,
         to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as created_at
  from public.users where id = %s
"""


async def _jwt_email(authorization: str | None = Header(default=None)) -> str:
    """從 Authorization: Bearer <jwt> 解 email claim（給 GET /me 用）。

    decode 失敗 / 無 email claim → 回空字串（不丟錯，GET /me 仍可回其他欄位）。
    dev bypass 模式沒 JWT → 回空字串。
    """
    settings = get_settings()
    # dev bypass：沒真 JWT，自然也沒 email
    if (
        settings.environment == "dev"
        and settings.dev_auth_bypass
        and settings.dev_user_id
        and (authorization is None or authorization.lower() == "bearer dev")
    ):
        return ""
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    token = authorization[7:].strip()
    if not token:
        return ""
    try:
        payload = _decode_payload(token)
    except AuthError:
        return ""
    email = payload.get("email")
    return str(email) if email else ""


@router.get("/me", response_model=ApiResponse[AccountInfo])
async def get_me_ep(
    user_id: str = Depends(get_current_user),
    email: str = Depends(_jwt_email),
) -> ApiResponse[AccountInfo]:
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_SELECT, (user_id,))
        row = await cur.fetchone()
    return ok(_row_to_account(row, user_id, email))


def _row_to_account(
    row: dict[str, Any] | None, user_id: str, email: str
) -> AccountInfo:
    if row is None:
        # handle_new_user trigger 尚未補列（極少見，初次註冊到第一次 SELECT 之間的極短窗口）
        # → 回 trigger 預設值 + JWT 提供的 id/email
        return AccountInfo(id=user_id, email=email)
    return AccountInfo(
        id=str(row["id"]),
        email=email,
        tz=str(row["tz"]),
        delivery_time=str(row["delivery_time"]),
        created_at=str(row["created_at"]),
    )


@router.delete("/me", response_model=ApiResponse[None])
async def delete_me_ep(user_id: str = Depends(get_current_user)) -> ApiResponse[None]:
    """刪除本人帳號。FK ON DELETE CASCADE 自動清 8 張 child tables。

    整個 DELETE 在同一 connection / 同一 transaction 內執行（commit 1 次），
    任一步失敗 → psycopg context manager 自動 rollback，無半毀狀態。
    """
    async with connection() as conn, conn.cursor() as cur:
        await cur.execute("delete from public.users where id = %s", (user_id,))
        await conn.commit()
    return ok(None)