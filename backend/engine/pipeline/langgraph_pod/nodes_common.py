"""共用小工具：跨 pipeline 階段（research/writer/judge/episode）共用的 config/metrics/usage 存取。

`_ctx`/`_collector` 是每個 node 的第一行都要呼叫的 boilerplate；
`_record_llm_usage` 是每個 chat.ainvoke() 呼叫點都該接的 usage 記錄 helper。
"""

from __future__ import annotations

import time
from typing import Any, cast

from langchain_core.runnables import RunnableConfig

from .metrics import MetricsCollector

# ── 對應 config["configurable"] 的 runtime context ─────────


def _ctx(config: RunnableConfig) -> dict[str, Any]:
    """從 RunnableConfig 取出 runtime context，缺欄位時 raise 提醒配置錯誤。"""
    configurable = config.get("configurable") or {}
    if not configurable:
        raise RuntimeError(
            "LangGraph pod 沒收到 configurable context；"
            "請用 run_pod(body, settings) 進入點，不要直接 graph.invoke({})."
        )
    return configurable


def _collector(config: RunnableConfig) -> MetricsCollector | None:
    """metrics collector 未接（測試直接 graph.ainvoke 沒帶 configurable）時回 None。"""
    return cast(MetricsCollector | None, _ctx(config).get("metrics_collector"))


def _usage_from_ai_msg(ai_msg: Any) -> dict[str, int]:
    """從 chat.py 塞進 AIMessage.usage_metadata 的量抽出來；缺欄位時回 0。"""
    meta = getattr(ai_msg, "usage_metadata", None) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
        "cache_creation_tokens": int(meta.get("cache_creation_input_tokens", 0)),
        "cache_read_tokens": int(meta.get("cache_read_input_tokens", 0)),
    }


def _record_llm_usage(
    collector: MetricsCollector | None,
    ai_msg: Any,
    *,
    node: str,
    call: str,
    call_start: float,
    attempt: int = 1,
    segment_index: int | None = None,
) -> dict[str, int]:
    """讀 ai_msg.usage_metadata，寫一筆 collector.record_llm_call；回傳 usage 供呼叫端累加。

    每個 chat.ainvoke() 呼叫點都該接這一行，取代手動組 record_llm_call 的樣板——
    漏貼樣板就是 cost 對某個節點隱形的根因（_normalize_line_lengths 曾經漏記，
    _generate_segment 曾經漏記 cache 欄位，都是同一種手動複製貼上失誤）。
    """
    usage = _usage_from_ai_msg(ai_msg)
    if collector is not None:
        collector.record_llm_call(
            node=node,
            call=call,
            attempt=attempt,
            duration_ms=int((time.monotonic() - call_start) * 1000),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_tokens=usage["cache_creation_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            segment_index=segment_index,
        )
    return usage
