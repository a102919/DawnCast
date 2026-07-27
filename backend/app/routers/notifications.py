"""Web Push 訂閱 router。

一台裝置一列 push_subscriptions（endpoint 為 PK，push service 保證全域唯一）。

設計取捨：
- 沒有「通知開關」欄位：關閉＝刪掉這台裝置的訂閱，開啟＝重新 subscribe。
  Notification.permission 一旦 granted 就不會再彈窗，所以重開不騷擾使用者；
  代價是多裝置各自獨立（這台關不影響另一台），這正是使用者預期的語意。
- 「已通知」狀態不放這層：deliveries.notified_at 由 worker 的 push_daily
  以單條 UPDATE ... WHERE notified_at is null 認領（見 repo.claim_daily_notifications），
  cron 掃幾次都只推一次。
- 取消訂閱用 DELETE + body：endpoint 是 2048 字元上限的 URL，塞進 path 會撞
  代理層限制，也得處理雙重 encode。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.response import ApiResponse, ok
from app.schemas import PushSubscribeBody, PushUnsubscribeBody
from shared.db import repo

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.post("/subscription", response_model=ApiResponse[None])
async def subscribe_push(
    body: PushSubscribeBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    """登錄這台裝置的訂閱（冪等 upsert）。"""
    await repo.upsert_push_subscription(user_id, body.endpoint, body.keys.p256dh, body.keys.auth)
    return ok(None)


@router.delete("/subscription", response_model=ApiResponse[None])
async def unsubscribe_push(
    body: PushUnsubscribeBody, user_id: str = Depends(get_current_user)
) -> ApiResponse[None]:
    """取消這台裝置的訂閱。找不到列也回成功（冪等）。"""
    await repo.delete_push_subscription(user_id, body.endpoint)
    return ok(None)
