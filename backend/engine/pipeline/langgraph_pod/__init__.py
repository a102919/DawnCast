"""LangGraph Pod 公開介面。

`run_pod(body, settings, *, use_mock=False)` 是 worker.py 與
scripts/run_langgraph_pod.py 共用的單一入口。

build_pod() 給需要直接 graph.compile() 客製化的人用（測試、demo、
checkpointer 替換）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import MemorySaver

from engine.pipeline import reuse_repo as db_repo
from engine.sources.factory import make_source_provider
from shared.config import Settings, get_settings
from shared.db import pool as db_pool
from shared.db import queue as db_queue
from shared.errors import RateLimitError
from shared.idempotency import compute_idempotency_key
from shared.models import ClaimVerification
from shared.storage import r2 as db_r2

from .chat import make_langchain_chat
from .graph import build_pod
from .metrics import MetricsCollector
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
        # backfill_dict_node 期待 queue_obj.send(queue_name, body) 介面；shared.db.queue
        # 模組本身的 send 函式簽名一致（差 self），模組就是「queue 物件」最簡實作。
        # 之前塞 None 會走 fallback 從 engine.pipeline.post_process 呼叫 backfill_dict()，
        # 那條在 graph 內同步跑會撞 connection pool 時序差（→ 雜訊 log）。
        queue_obj = db_queue
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
    enqueued_at: datetime | None = None,
    channel_id: str | None = None,
    channel_topic_id: str | None = None,
    series_context: list[str] | None = None,
) -> str | None:
    """跑一集 LangGraph pod，回傳 episode_id；storage 雙重失敗優雅結束時回 None。

    用法：
      * production：worker.py 呼叫 `run_pod(body)`，use_mock 自動 False。
      * demo：scripts/run_langgraph_pod.py 帶 `--mock` 走 in-memory。
      * 測試：注入 FakeChatModel 等 fixtures（任意一個被注入就自動進 mock 模式，
        不會去開 DB pool）。要測 grounding 行為時額外注入 source_provider_factory。

    enqueued_at：pgmq 訊息入列時間，從 worker 一路傳進來算 queue_wait_ms
    （見 metrics.py）。demo / 直接呼叫時可留 None。

    channel_id / channel_topic_id / series_context：頻道機制專用，keyword-only
    （非 body 欄位）——generate_job.py 是唯一負責從 pgmq body 解出這三欄再轉呼叫
    的 thin shim。刻意不放進 body 直接讀取（不像 big_topic/angle 等既有欄位），
    是要讓「頻道相關的呼叫端」以後加新 axis 時不會因為參數順序或字典 key 打錯字
    而默默送錯欄位（見 lessons.md 2026-07-15「keyword-only 是好朋友」）。

    回傳 None 只發生在 dead_letter_node 那條路徑（storage 上傳雙重失敗、row
    已被刪除、pipeline_runs 已 finalize 成 dead_letter）：這是刻意的優雅結束，
    不 raise 是為了不讓 worker vt-retry 把已經跑完的 33s+ TTS render 重做一次；
    呼叫端不能把 None 當例外處理，只能當「這次沒有集數產出」。
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

    # idem_key 這裡先算一份給 collector / forensic run 用，
    # upsert_episode_node 內仍是唯一權威來源。
    idem_key = compute_idempotency_key(
        cluster_id=body.get("cluster_id"),
        deliver_date=body["deliver_date"],
        big_topic=body["big_topic"],
        angle=body.get("angle"),
        length_tier=body.get("length_tier"),
        topic_type=body.get("topic_type"),
    )
    collector = MetricsCollector(idempotency_key=idem_key, enqueued_at=enqueued_at)
    run_id = await runtime["repo"].start_pipeline_run(idem_key, enqueued_at=enqueued_at)
    runtime["metrics_collector"] = collector
    runtime["pipeline_run_id"] = run_id

    async def _finalize_run_failed(exc_type: str, message: str) -> None:
        collector.finalize(
            "failed", error={"node": "run_pod", "type": exc_type, "message": message[:500]}
        )
        try:
            await runtime["repo"].finalize_pipeline_run(
                run_id,
                status="failed",
                gen_metrics=collector.gen_metrics(),
                research_metrics=collector.research_metrics(),
                error=collector.error,
            )
        except Exception:
            logger.exception("finalize_pipeline_run 失敗 run_id=%s", run_id)

    # 初始 state：解開 body 為 PodState 欄位
    initial: dict[str, Any] = {
        "body": body,
        "big_topic": body["big_topic"],
        "canonical_topic": body.get("canonical_topic") or body["big_topic"],
        "angle": body.get("angle") or "定義",
        "topic_type": body.get("topic_type") or "evergreen",
        "source": body.get("source") or "fallback",
        "deliver_date": body["deliver_date"],
        "user_ids": list(body.get("user_ids") or []),
        "cluster_id": body.get("cluster_id"),
        "length_tier": body.get("length_tier") or "medium",
        "cefr": body.get("cefr") or cfg.cefr_level,
        "avoid_facts": list(body.get("avoid_facts") or []),
        "order_id": body.get("order_id"),
        "channel_id": channel_id,
        "channel_topic_id": channel_topic_id,
        "series_context": list(series_context or []),
        "sources": [],
        "grounded": False,
        "research_questions": [],
        "evidence_cards": [],
        "verified_claims": [],
        "source_conflicts": [],
        "claim_verification": ClaimVerification(checks=[], unsupported_ratio=0.0),
        "rewrite_iterations": 0,
        "judge_feedback": [],
        "token_usage": [],
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
    except Exception as exc:
        # graph 整個炸掉（多半發生在 upsert_episode 之前，例如 write_script 重試
        # 耗盡）：episode row 可能還沒建立，forensic run row 是唯一留得住的紀錄。
        await _finalize_run_failed(type(exc).__name__, str(exc))
        raise
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
        errors = final.get("errors") or []
        if final.get("storage_failed"):
            # dead_letter_node 已經自己刪掉半完成 row、把 pipeline_runs
            # finalize 成 status="dead_letter"（不是 "failed"）。這裡不能再呼叫
            # _finalize_run_failed——會用 status="failed" 蓋掉剛寫的正確狀態，
            # 且不能 raise：raise 會讓 worker 判定失敗去重投，白白把已經跑完的
            # 33s+ TTS render 整個重做一次，對系統性 R2 故障沒有幫助。
            logger.warning("pod 因 storage 上傳失敗優雅結束，未產出 episode：errors=%s", errors)
            return None
        # 走到 END 沒拿到 episode_id 代表 fail（典型：degrade 模式撞限流放棄）。
        # worker 端 vt-retry 機制會接手重投；rate limit 也要明確 raise 讓
        # production 觀測能正確分流。這條路徑通常發生在 upsert_episode 之前，
        # 尚未有其他 node 幫忙 finalize forensic run，這裡補上。
        await _finalize_run_failed("NoEpisodeId", str(errors))
        if final.get("rate_limited"):
            raise RateLimitError(f"pod 限流且未啟用 failover：{errors}")
        raise RuntimeError(f"pod 沒產出 episode_id：errors={errors}")
    return cast(str, episode_id)


__all__ = ["build_pod", "run_pod"]
