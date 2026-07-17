"""出餐通知 router（T8）。

範圍限縮：查過 shared/config.py 與 .env.example，目前完全沒有 email 相關
套件 / API key 設定（無 SMTP/SendGrid/Resend/SES 欄位），T1 的排程觸發
機制（jobs.py）也尚未建立。故本次只實作「到 settings.defaultDeliveryTime
→ 產生待寄通知記錄」的觸發邏輯與其單元測試，不假造外部寄信串接。

已定案維持唯讀觀察端點，出餐通知功能不會再接寄信整合（產品決策，見 tasks/todo.md T8）。

設計取捨（見 tasks/todo.md）：
- 不建 DB 表存「已通知」狀態——目前沒有任何消費者會讀寫它，現在建表等於
  預先猜 schema。代價：本端點目前無去重能力，若之後直接拿它當 dispatch
  trigger 源頭，須先補去重機制，否則會重複寄信。
- should_notify 用「分鐘精確比對」而非「now >= delivery_time」，避免在
  沒有去重狀態的情況下整天重複觸發；代價是排程沒精準命中該分鐘就會整天
  錯過，之後要保證寄達須改用有狀態的 idempotent 設計。
- GET（唯讀觀測）而非 POST（觸發動作）：目前完全沒有寄信副作用。
- 沿用 admin.py 既有的 X-Admin-Token 授權（ops/系統觸發情境慣例），不走
  一般 Supabase JWT。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from psycopg.rows import dict_row

from app.response import ApiResponse, ok
from app.routers.admin import require_admin_token
from shared.config import get_settings
from shared.db.pool import connection
from shared.models import CamelModel


@dataclass(frozen=True)
class UserDeliveryState:
    """單一使用者的通知判斷輸入：設定的送達時間 + 今天是否已有 delivery。"""

    user_id: str
    delivery_time: str  # 'HH:MM'
    has_delivery: bool


@dataclass(frozen=True)
class PendingNotification:
    """該通知的使用者。"""

    user_id: str
    delivery_time: str


def should_notify(now: datetime, delivery_time: str, *, has_delivery: bool) -> bool:
    """觸發規則：now 的牆鐘時間（HH:MM）精確等於 delivery_time，且今天已有 delivery。

    分鐘精確比對，不是「>= 就一直觸發」——沒有去重狀態，用區間比對會導致
    同一天內任何時刻呼叫都重複回報。delivery_time 格式不合法時防禦性回
    False，不拋例外（避免一筆髒資料炸掉整批掃描）。
    """
    if not has_delivery:
        return False
    try:
        hour_str, minute_str = delivery_time.split(":")
        target = time(hour=int(hour_str), minute=int(minute_str))
    except (ValueError, AttributeError):
        return False
    return now.hour == target.hour and now.minute == target.minute


def build_pending_notifications(
    now: datetime, states: Iterable[UserDeliveryState]
) -> list[PendingNotification]:
    """過濾出此刻該收到通知的使用者清單。"""
    return [
        PendingNotification(user_id=s.user_id, delivery_time=s.delivery_time)
        for s in states
        if should_notify(now, s.delivery_time, has_delivery=s.has_delivery)
    ]


class PendingNotificationOut(CamelModel):
    user_id: str
    delivery_time: str


router = APIRouter(
    prefix="/notifications", tags=["notifications"], dependencies=[Depends(require_admin_token)]
)


_STATES_SQL = """
  select
    us.user_id::text as user_id,
    to_char(us.default_delivery_time, 'HH24:MI') as default_delivery_time,
    exists (
      select 1 from public.deliveries d
      where d.user_id = us.user_id and d.deliver_date = %s
    ) as has_delivery
  from public.user_settings us
"""


def _now_taipei() -> datetime:
    settings = get_settings()
    return datetime.now(ZoneInfo(settings.app_timezone))


@router.get("/pending", response_model=ApiResponse[list[PendingNotificationOut]])
async def list_pending_notifications() -> ApiResponse[list[PendingNotificationOut]]:
    """目前該收到出餐通知的使用者清單（唯讀觀測，尚未接上實際寄送）。"""
    now = _now_taipei()
    async with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_STATES_SQL, (now.date(),))
        rows: list[dict[str, Any]] = await cur.fetchall()

    states = [
        UserDeliveryState(
            user_id=r["user_id"],
            delivery_time=r["default_delivery_time"],
            has_delivery=r["has_delivery"],
        )
        for r in rows
    ]
    pending = build_pending_notifications(now, states)
    return ok(
        [
            PendingNotificationOut(user_id=p.user_id, delivery_time=p.delivery_time)
            for p in pending
        ]
    )
