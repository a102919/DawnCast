"""每日排程觸發 router（T1）。

使用者送訂單後呼叫 POST /jobs/orders/{date}/generate：
  1. 查 daily_order 當前 status
     - 找不到 → 404 NotFoundError
     - queued / played → 409 ConflictError（不重複觸發）
  2. status=pending → atomic conditional UPDATE 翻 queued
     - 並發第二個請求 rowcount=0 → 409（零應用層鎖，SQL 層 CAS）
  3. enqueue pgmq control orchestrate {date} 給 worker._handle_control 接手
     - 即使 send 失敗 22:00 collect_open cron 仍會撿走 pending，吞 log 即可

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
    "/orders/{date}/generate",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ApiResponse[dict[str, str]],
)
async def trigger_order_generate(
    date: str, user_id: str = Depends(get_current_user)
) -> ApiResponse[dict[str, str]]:
    """送一筆 control orchestrate 給 worker，觸發當日 episode pipeline。

    回 202 Accepted：job 已 enqueue，不代表已生成；GET /daily-orders/{date}/episode
    輪詢結果。
    """
    current = await repo.get_order_status(user_id, date)
    if current is None:
        raise NotFoundError("查無當日訂單，請先下單")
    if current != "pending":
        # 已 queued（22:00 collect_open 翻過）或 played → 不重複觸發
        raise ConflictError(f"訂單狀態為 {current}，不重複觸發")

    # SQL 層 CAS：並發第二個請求會 rowcount=0 → 409
    flipped = await repo.transition_order_to_queued(user_id, date)
    if not flipped:
        raise ConflictError("訂單已被其他請求觸發，請稍候")

    # enqueue 失敗時 swallow + log：22:00 collect_open cron 仍會撿走 pending，
    # 回 5xx 反而誤導前端以為需要重試。
    try:
        await queue.send("control", {"task": "orchestrate", "date": date})
    except Exception:
        logger.exception("enqueue control orchestrate 失敗（date=%s, user=%s）", date, user_id)

    return ok({"date": date, "status": "queued"})