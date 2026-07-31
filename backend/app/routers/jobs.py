"""點餐即時觸發 router：使用者送出訂單後呼叫 POST /jobs/orders/{order_id}/generate。

  1. 查 daily_order 當前 status
     - 找不到 → 404 NotFoundError
     - queued / played → 409 ConflictError（不重複觸發）
  2. status=pending → atomic conditional UPDATE 翻 queued
     - 並發第二個請求 rowcount=0 → 409（零應用層鎖，SQL 層 CAS）
  3. enqueue pgmq control orchestrate {order_id, date} 給 worker._handle_control 接手
     - 即使 send 失敗也不報 5xx：dawncast-order-reconcile（每 5 分鐘）會撿走
       卡在 pending 太久的訂單重放觸發（見 migration 0024 / worker.py）

授權：Depends(get_current_user)，user_id 從 JWT 取，不信任 path。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status

from app.deps import get_current_user
from app.response import ApiResponse, ok
from shared.db import queue, repo
from shared.errors import ConflictError, NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/orders/{order_id}/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[dict[str, str]],
)
async def trigger_order_generate(
    order_id: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[dict[str, str]]:
    """送一筆 control orchestrate 給 worker，觸發這筆訂單的 episode pipeline。

    回 202 Accepted：job 已 enqueue，不代表已生成；GET /daily-orders/{order_id}/episode
    輪詢結果。
    """
    current = await repo.get_order_status(user_id, order_id)
    if current is None:
        raise NotFoundError("查無此訂單，請先下單")
    if current != "pending":
        raise ConflictError(f"訂單狀態為 {current}，不重複觸發")

    # SQL 層 CAS：並發第二個請求會 rowcount=0 → 409
    flipped = await repo.transition_order_to_queued(user_id, order_id)
    if not flipped:
        raise ConflictError("訂單已被其他請求觸發，請稍候")

    order_date = await repo.get_order_date(order_id)

    # enqueue 失敗時 swallow + log：此時 CAS 已翻 queued，reconcile 的
    # stuck_queued 墊檔（20 分鐘無 delivery → 補常青集）會兜住；回 5xx
    # 反而誤導前端以為需要重試（重試會撞 CAS 409）。
    try:
        await queue.send(
            "control", {"task": "orchestrate", "order_id": order_id, "date": order_date}
        )
    except Exception:
        logger.exception(
            "enqueue control orchestrate 失敗（order_id=%s, user=%s）", order_id, user_id
        )

    return ok({"orderId": order_id, "status": "queued"})
