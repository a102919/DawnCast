"""LangGraph Pod 公開介面。

`run_pod(body, settings, *, use_mock=False)` 是 worker.py 與
scripts/run_langgraph_pod.py 共用的單一入口。

build_pod() 給需要直接 graph.compile() 客製化的人用（測試、demo、
checkpointer 替換）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from engine.sources.factory import make_source_provider
from shared.config import Settings, get_settings
from shared.db import pool as db_pool
from shared.db import repo as db_repo
from shared.errors import RateLimitError
from shared.storage import r2 as db_r2

from .chat import make_langchain_chat
from .graph import build_pod
from .mock import MockRenderer, get_mocks

SourceProviderFactory = Callable[[str, Settings], Any]

logger = logging.getLogger(__name__)


def _build_runtime_context(
    settings: Settings,
    *,
    use_mock: bool,
    reset_mocks: bool = True,
) -> dict[str, Any]:
    """組 config['configurable']：chat / chat_failover / repo / r2 / queue / renderer / settings。

    use_mock=True → 全部走 mock；False → 走 production infra（DB / R2）。
    reset_mocks=True → 用內部 get_mocks(reset=True)（demo 單 process 用）。
    reset_mocks=False → 用 caller 已注入的 singleton（測試跨多次 run_pod 共享狀態用）。
    """
    if use_mock:
        repo_obj: Any
        r2_obj: Any
        queue_obj: Any
        renderer_obj: Any
        repo_obj, r2_obj, queue_obj = get_mocks(reset=reset_mocks)
        renderer_obj = MockRenderer
        chat: BaseChatModel | None = None
        chat_failover: BaseChatModel | None = None
        # mock 模式預設不打真實資料來源；測試要驗證 grounding 行為時
        # 透過 run_pod(source_provider_factory=...) 明確注入 stub。
        source_provider_factory: SourceProviderFactory | None = None
    else:
        repo_obj = db_repo
        r2_obj = db_r2
        queue_obj = None
        renderer_obj = None
        chat = make_langchain_chat(settings, engine=settings.generation_engine)
        chat_failover = (
            make_langchain_chat(settings, engine="api_key")
            if settings.failover_mode == "failover"
            else None
        )
        source_provider_factory = make_source_provider

    return {
        "chat": chat,
        "chat_failover": chat_failover,
        "repo": repo_obj,
        "r2": r2_obj,
        "queue": queue_obj,
        "renderer": renderer_obj,
        "settings": settings,
        "failover_mode": settings.failover_mode,
        "quality_threshold": settings.quality_threshold,
        "max_rewrite_iterations": settings.max_rewrite_iterations,
        "source_provider_factory": source_provider_factory,
    }


async def run_pod(
    body: dict[str, Any],
    settings: Settings | None = None,
    *,
    use_mock: bool = False,
    chat: BaseChatModel | None = None,
    chat_failover: BaseChatModel | None = None,
    renderer: MockRenderer | None = None,
    repo: Any = None,
    r2: Any = None,
    queue: Any = None,
    source_provider_factory: SourceProviderFactory | None = None,
    thread_id: str | None = None,
) -> str:
    """跑一集 LangGraph pod，回傳 episode_id。

    用法：
      * production：worker.py 呼叫 `run_pod(body)`，use_mock 自動 False。
      * demo：scripts/run_langgraph_pod.py 帶 `--mock` 走 in-memory。
      * 測試：注入 FakeChatModel 等 fixtures（任意一個被注入就自動進 mock 模式，
        不會去開 DB pool）。要測 grounding 行為時額外注入 source_provider_factory。
    """
    cfg = settings or get_settings()
    # 任何元件被注入 → 視為測試 / mock 模式，不開 DB pool，也不 reset mock state
    # （讓 caller 在多次 run_pod 間保留 by_idem / deliveries 等狀態以測冪等）。
    injected = any(
        x is not None
        for x in (chat, chat_failover, renderer, repo, r2, queue, source_provider_factory)
    )
    effective_mock = use_mock or injected
    if not effective_mock:
        await db_pool.open_pool()

    runtime = _build_runtime_context(cfg, use_mock=effective_mock, reset_mocks=not injected)
    # 允許測試 / demo 覆寫 chat / repo / 等
    if chat is not None:
        runtime["chat"] = chat
    if chat_failover is not None:
        runtime["chat_failover"] = chat_failover
    if renderer is not None:
        runtime["renderer"] = renderer
    if repo is not None:
        runtime["repo"] = repo
    if r2 is not None:
        runtime["r2"] = r2
    if queue is not None:
        runtime["queue"] = queue
    if source_provider_factory is not None:
        runtime["source_provider_factory"] = source_provider_factory

    # 初始 state：解開 body 為 PodState 欄位
    initial: dict[str, Any] = {
        "body": body,
        "big_topic": body["big_topic"],
        "canonical_topic": body.get("canonical_topic") or body["big_topic"],
        "angle": body.get("angle") or "定義",
        "topic_type": body.get("topic_type") or "evergreen",
        "deliver_date": body["deliver_date"],
        "user_ids": list(body.get("user_ids") or []),
        "cluster_id": body.get("cluster_id"),
        "length_tier": body.get("length_tier") or "medium",
        "rewrite_iterations": 0,
        "judge_feedback": [],
        "errors": [],
        "rate_limited": False,
        "storage_failed": False,
        "already_rendered": False,
    }

    graph = build_pod(checkpointer=MemorySaver())
    config: Any = {
        "configurable": {
            **runtime,
            "thread_id": (
                thread_id or body.get("cluster_id") or f"{body['deliver_date']}:{body['big_topic']}"
            ),
        },
    }

    try:
        final: Any = await graph.ainvoke(initial, config=config)
    finally:
        if not effective_mock:
            chat_obj = runtime.get("chat")
            chat_fo = runtime.get("chat_failover")
            if chat_obj is not None and hasattr(chat_obj, "aclose"):
                await chat_obj.aclose()
            if chat_fo is not None and hasattr(chat_fo, "aclose"):
                await chat_fo.aclose()

    episode_id = final.get("episode_id")
    if not episode_id:
        # 走到 END 沒拿到 episode_id 代表 fail（典型：degrade 模式撞限流放棄）。
        # worker 端 vt-retry 機制會接手重投；rate limit 也要明確 raise 讓
        # production 觀測能正確分流。
        errors = final.get("errors") or []
        if final.get("rate_limited"):
            raise RateLimitError(f"pod 限流且未啟用 failover：{errors}")
        raise RuntimeError(f"pod 沒產出 episode_id：errors={errors}")
    return cast(str, episode_id)


__all__ = ["build_pod", "run_pod"]
