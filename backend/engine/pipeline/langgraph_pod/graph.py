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

import functools
import inspect
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from shared.errors import GenerationError

from .nodes import (
    backfill_dict_node,
    cross_verify_node,
    dead_letter_node,
    decompose_research_node,
    failover_decision,
    failover_write_script_node,
    gather_evidence_node,
    insert_deliveries_node,
    judge_decision,
    quality_judge_node,
    rate_limit_decision,
    render_branch_decision,
    render_episode_node,
    rewrite_iteration_bump_node,
    storage_decision,
    tone_selector_node,
    update_episode_keys_node,
    upload_artifacts_node,
    upsert_episode_node,
    verify_script_claims_node,
    write_script_node,
)
from .state import PodState

_WRITER_RETRY = RetryPolicy(
    max_attempts=3,
    initial_interval=0.5,
    backoff_factor=2.0,
    retry_on=GenerationError,
)


def _timed(name: str, fn: Any) -> Any:
    """把每個 node 包一層計時：讀 config['configurable']['metrics_collector']。

    collector 未接（測試直接 graph.ainvoke 沒帶 configurable）時原樣直通，不影響行為。
    RetryPolicy 重試會讓同一個 node 被呼叫多次，collector.stage() 內部依 attempt 遞增
    區分每次重試的耗時。tone_selector_node 是同步函式，其餘皆 async——用
    iscoroutinefunction 判斷呼叫方式，wrapper 本身固定回傳 coroutine（LangGraph
    支援 sync/async node，包一層後一律視為 async 不影響行為）。

    回傳型別刻意標 Any：StateGraph.add_node 的 overload 要求跟原始 node function
    完全一致的具名 Callable 結構，包一層 wraps 後的 closure 即使結構相同也會撞
    overload 解析失敗；這是 wrap-a-third-party-typed-API 的已知邊界，不是型別錯誤。
    """
    is_async = inspect.iscoroutinefunction(fn)

    @functools.wraps(fn)
    async def wrapper(state: PodState, config: RunnableConfig) -> dict[str, Any]:
        collector = (config.get("configurable") or {}).get("metrics_collector")
        result: dict[str, Any]
        if collector is None:
            result = await fn(state, config) if is_async else fn(state, config)
        else:
            with collector.stage(name):
                result = await fn(state, config) if is_async else fn(state, config)
        return result

    return wrapper


def build_pod(*, checkpointer: MemorySaver | None = None) -> Any:
    """組出 CompiledStateGraph。

    checkpointer 預設 MemorySaver（demo / test 用）。
    Production 想用 PostgresSaver 時呼叫端注入；目前 V1 不啟用。
    """
    builder = StateGraph(PodState)

    # ── nodes ─────────────────────────────────────────────
    # 主線走四段研究管線：decompose_research → gather_evidence → cross_verify → tone_selector。
    # 研究節點刻意不掛 RetryPolicy：任何外部/LLM 失敗由節點內安全降級。
    # 每個 node 用 _timed() 包一層記分階段耗時（見 metrics.py）。
    builder.add_node("decompose_research", _timed("decompose_research", decompose_research_node))
    builder.add_node("gather_evidence", _timed("gather_evidence", gather_evidence_node))
    builder.add_node("cross_verify", _timed("cross_verify", cross_verify_node))
    builder.add_node("tone_selector", _timed("tone_selector", tone_selector_node))
    builder.add_node(
        "write_script",
        _timed("write_script", write_script_node),
        retry_policy=_WRITER_RETRY,
    )
    builder.add_node(
        "failover_write_script",
        _timed("failover_write_script", failover_write_script_node),
        retry_policy=_WRITER_RETRY,
    )
    builder.add_node(
        "verify_script_claims", _timed("verify_script_claims", verify_script_claims_node)
    )
    builder.add_node("quality_judge", _timed("quality_judge", quality_judge_node))
    builder.add_node("rewrite_iter_bump", _timed("rewrite_iter_bump", rewrite_iteration_bump_node))
    builder.add_node("upsert_episode", _timed("upsert_episode", upsert_episode_node))
    builder.add_node("render_episode", _timed("render_episode", render_episode_node))
    builder.add_node("upload_artifacts", _timed("upload_artifacts", upload_artifacts_node))
    builder.add_node("dead_letter", _timed("dead_letter", dead_letter_node))
    builder.add_node("update_episode_keys", _timed("update_episode_keys", update_episode_keys_node))
    builder.add_node("insert_deliveries", _timed("insert_deliveries", insert_deliveries_node))
    builder.add_node("backfill_dict", _timed("backfill_dict", backfill_dict_node))

    # ── edges ─────────────────────────────────────────────
    builder.add_edge(START, "decompose_research")
    builder.add_edge("decompose_research", "gather_evidence")
    builder.add_edge("gather_evidence", "cross_verify")
    builder.add_edge("cross_verify", "tone_selector")
    builder.add_edge("tone_selector", "write_script")

    # write_script 出來分三路：主張核對 / failover / END
    builder.add_conditional_edges(
        "write_script",
        rate_limit_decision,
        {
            "judge": "verify_script_claims",
            "failover": "failover_write_script",
            END: END,
        },
    )

    # failover 出來再分：judge / END
    builder.add_conditional_edges(
        "failover_write_script",
        failover_decision,
        {
            "judge": "verify_script_claims",
            END: END,
        },
    )

    builder.add_edge("verify_script_claims", "quality_judge")

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
    # upload_artifacts 後分流：storage_failed + 無本地 fallback → dead_letter_node
    # 做 DELETE + graceful END，避免 update_episode_keys 雙重失敗 raise 觸發
    # LangGraph 整個 invoke 失敗 → worker vt 重投 → render 重做。
    builder.add_conditional_edges(
        "upload_artifacts",
        storage_decision,
        {
            "update_keys": "update_episode_keys",
            "dead_letter": "dead_letter",
        },
    )
    builder.add_edge("dead_letter", END)
    builder.add_edge("update_episode_keys", "insert_deliveries")
    builder.add_edge("insert_deliveries", "backfill_dict")
    builder.add_edge("backfill_dict", END)

    saver = checkpointer or MemorySaver()
    return builder.compile(checkpointer=saver)
