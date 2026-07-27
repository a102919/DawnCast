"""Web Push（VAPID）送出層。

設計取捨：
- VAPID key 留空 → notify_user 直接回 0，不碰 DB。dev / 測試 / mock pipeline
  天然安全，不需要在每個 caller 加 if 判斷。
- pywebpush 是同步套件（內部 requests），統一包 asyncio.to_thread 隔離 event loop。
- 404 / 410 = 訂閱已被瀏覽器或 push service 撤銷，唯一正確反應是刪列。
  其他非 2xx 只記 warning。
- ponytail: 不做 retry。通知漏一則不是 data loss，5xx 直接放棄；要保證送達
  再補指數退避（同時得處理「已 claim 但沒送到」，不然重試會重複通知）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal

from pywebpush import WebPushException, webpush

from shared.config import get_settings
from shared.db import repo

logger = logging.getLogger(__name__)

# 外部 HTTP timeout（全域規則：connect 5s / read 30s）。pywebpush 原樣轉給 requests。
_TIMEOUT = (5, 30)

# 已撤銷的訂閱：push service 明確表示這個 endpoint 不再存在。
_GONE_STATUSES = frozenset({404, 410})

SendOutcome = Literal["ok", "gone", "failed"]


async def notify_user(user_id: str, payload: dict[str, str]) -> int:
    """推一則通知給該 user 的所有裝置。回傳成功送達的裝置數。

    payload 形狀由 frontend/public/push-sw.js 消費：{title, body, url}。
    """
    settings = get_settings()
    if not (settings.vapid_private_key and settings.vapid_public_key):
        return 0

    subs = await repo.list_push_subscriptions(user_id)
    if not subs:
        return 0

    data = json.dumps(payload, ensure_ascii=False)
    results = await asyncio.gather(
        *(_send_one(sub, data, settings.vapid_private_key, settings.vapid_subject) for sub in subs)
    )

    dead = [endpoint for endpoint, outcome in results if outcome == "gone"]
    if dead:
        await repo.delete_push_endpoints(dead)
        logger.info("清掉 %d 個已撤銷的 push 訂閱（user=%s）", len(dead), user_id)
    return sum(1 for _, outcome in results if outcome == "ok")


async def _send_one(
    sub: dict[str, Any], data: str, private_key: str, subject: str
) -> tuple[str, SendOutcome]:
    endpoint = str(sub["endpoint"])
    info = {"endpoint": endpoint, "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
    try:
        await asyncio.to_thread(
            webpush,
            subscription_info=info,
            data=data,
            vapid_private_key=private_key,
            # pywebpush 會就地補 aud / exp，每次呼叫給新 dict。
            vapid_claims={"sub": subject},
            timeout=_TIMEOUT,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in _GONE_STATUSES:
            return endpoint, "gone"
        # endpoint 含裝置識別碼，只記 status 不記全文。
        logger.warning("push 送出失敗 status=%s", status)
        return endpoint, "failed"
    except Exception:
        logger.exception("push 送出異常")
        return endpoint, "failed"
    return endpoint, "ok"
