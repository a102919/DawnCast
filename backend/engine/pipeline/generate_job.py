"""消費單筆 generate 訊息的主流程（PRD §6）。

V2 改為 thin shim：把 body 轉交給 LangGraph pod 跑。

為什麼這樣改：
  * judge → rewrite 迴圈在 plain asyncio 裡要再寫一層 ad-hoc retry + 條件分支，
    LangGraph 的 `add_conditional_edges` + back-edge 是更直接的表達。
  * per-node `RetryPolicy` 把語意層重試（GenerationError）與傳輸重試（5xx）分開，
    不用再混在 `_write_script` 的 try/except 裡。

對外介面 `run_generate_job(body, settings) -> str` 維持不變，worker.py 與既有
tests/test_pipeline.py 透過 `**run_pod_kwargs` 注入 mocks（chat / repo / r2 /
queue / renderer）— production 呼叫端零改動。
"""

from __future__ import annotations

import logging
from typing import Any

from shared.config import Settings, get_settings

logger = logging.getLogger(__name__)


async def run_generate_job(
    body: dict[str, Any],
    settings: Settings | None = None,
    **run_pod_kwargs: Any,
) -> str:
    """處理一筆 generate 訊息，回傳建立的 episode_id。

    body：{big_topic, angle?, cluster_id?, deliver_date, user_ids[]}。
    冪等：失敗在落庫前 raise，worker 不 delete → vt 到期重投；成功後 worker delete。

    **run_pod_kwargs 給測試 / demo 注入 LangGraph pod 內部元件用：
      chat / chat_failover / repo / r2 / queue / renderer / use_mock。
    production 呼叫不傳這些，由 run_pod 從 Settings 自動組。
    """
    cfg = settings or get_settings()
    from engine.pipeline.langgraph_pod import run_pod  # noqa: PLC0415 lazy import

    episode_id = await run_pod(body, cfg, **run_pod_kwargs)
    logger.info(
        "generate job 完成 episode_id=%s big_topic=%s 收件 %d 人",
        episode_id,
        body["big_topic"],
        len(body.get("user_ids") or []),
    )
    return episode_id
