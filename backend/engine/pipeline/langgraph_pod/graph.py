"""LangGraph Pod 的 StateGraph compiler。

build_pod() 組好編譯過的 CompiledStateGraph。runtime context（chat / repo /
renderer / settings）由 invoke 時透過 config["configurable"] 傳入，graph
本身 stateless（可平行處理多集，每集一個 thread_id）。

RetryPolicy 對照 production 行為：
  * write_script_node     → GenerationError 重試 3 次（PRD 防重生風暴）
  * failover_write_script → GenerationError 重試 3 次
  * render_episode_node   → 不重試（ffmpeg 錯誤通常永久）
  * upload_artifacts_node → 不重試（StorageError 由 conditional fallback 處理）
  * 其餘                  → 不重試，DB 錯誤直接 propagate 給 vt-retry
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from shared.errors import GenerationError

from .nodes import (
    backfill_dict_node,
    failover_decision,
    failover_write_script_node,
    insert_deliveries_node,
    judge_decision,
    quality_judge_node,
    rate_limit_decision,
    render_branch_decision,
    render_episode_node,
    retrieve_sources_node,
    rewrite_iteration_bump_node,
    tone_selector_node,
    update_episode_keys_node,
    upload_artifacts_node,
    upsert_episode_node,
    write_script_node,
)
from .state import PodState

_WRITER_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    retry_on=GenerationError,
)


def build_pod(*, checkpointer: MemorySaver | None = None) -> Any:
    """組出 CompiledStateGraph。

    checkpointer 預設 MemorySaver（demo / test 用）。
    Production 想用 PostgresSaver 時呼叫端注入；目前 V1 不啟用。
    """
    builder = StateGraph(PodState)

    # ── nodes ─────────────────────────────────────────────
    builder.add_node("retrieve_sources", retrieve_sources_node)
    builder.add_node("tone_selector", tone_selector_node)
    builder.add_node(
        "write_script",
        write_script_node,
        retry_policy=_WRITER_RETRY,
    )
    builder.add_node(
        "failover_write_script",
        failover_write_script_node,
        retry_policy=_WRITER_RETRY,
    )
    builder.add_node("quality_judge", quality_judge_node)
    builder.add_node("rewrite_iter_bump", rewrite_iteration_bump_node)
    builder.add_node("upsert_episode", upsert_episode_node)
    builder.add_node("render_episode", render_episode_node)
    builder.add_node("upload_artifacts", upload_artifacts_node)
    builder.add_node("update_episode_keys", update_episode_keys_node)
    builder.add_node("insert_deliveries", insert_deliveries_node)
    builder.add_node("backfill_dict", backfill_dict_node)

    # ── edges ─────────────────────────────────────────────
    builder.add_edge(START, "retrieve_sources")
    builder.add_edge("retrieve_sources", "tone_selector")
    builder.add_edge("tone_selector", "write_script")

    # write_script 出來分三路：judge / failover / END
    builder.add_conditional_edges(
        "write_script",
        rate_limit_decision,
        {
            "judge": "quality_judge",
            "failover": "failover_write_script",
            END: END,
        },
    )

    # failover 出來再分：judge / END
    builder.add_conditional_edges(
        "failover_write_script",
        failover_decision,
        {
            "judge": "quality_judge",
            END: END,
        },
    )

    # quality_judge 出來分：upsert / rewrite
    builder.add_conditional_edges(
        "quality_judge",
        judge_decision,
        {
            "upsert": "upsert_episode",
            "rewrite": "rewrite_iter_bump",
        },
    )

    # rewrite 迴圈：bump → write_script（會讀 judge_feedback）
    builder.add_edge("rewrite_iter_bump", "write_script")

    # upsert 後看 already_rendered 分流
    builder.add_conditional_edges(
        "upsert_episode",
        render_branch_decision,
        {
            "render": "render_episode",
            "deliveries": "insert_deliveries",
        },
    )

    builder.add_edge("render_episode", "upload_artifacts")
    builder.add_edge("upload_artifacts", "update_episode_keys")
    builder.add_edge("update_episode_keys", "insert_deliveries")
    builder.add_edge("insert_deliveries", "backfill_dict")
    builder.add_edge("backfill_dict", END)

    saver = checkpointer or MemorySaver()
    return builder.compile(checkpointer=saver)
