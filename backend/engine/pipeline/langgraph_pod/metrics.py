"""LangGraph Pod 生成過程的分階段耗時 + 研究過程收集器。

`MetricsCollector` 是掛在 `config["configurable"]["metrics_collector"]` 的可變物件，
被每個 node 直接呼叫、直接寫入自己身上——刻意不透過 PodState reducer channel。

理由：node 拋例外時 LangGraph 不會把它的回傳 dict 併進 state（根本沒有回傳），
若靠 reducer 收集，pre-upsert 階段一旦例外，目前為止收集到的 stage timing 會跟著
整包遺失，跟現況「完全沒 metrics」沒兩樣——這正是這次要解決的痛點（研究節點失敗
時完全沒有紀錄）。掛在 ctx 上的物件不受這個限制：run_pod 的 except 分支仍讀得到
collector 目前已經累積的內容，可以照樣落 forensic row。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

_SCHEMA_VERSION = "1"


class MetricsCollector:
    """一次 run_pod 呼叫對應一個 collector 實例（不可跨集共用）。"""

    def __init__(self, *, idempotency_key: str, enqueued_at: datetime | None = None) -> None:
        self.idempotency_key = idempotency_key
        self.enqueued_at = enqueued_at
        self.started_at = datetime.now(UTC)
        self._start_monotonic = time.monotonic()
        self._stage_attempts: dict[str, int] = {}
        self.stages: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []
        self.research: dict[str, Any] = {}
        self.status = "running"
        self.finished_at: datetime | None = None
        self.error: dict[str, Any] | None = None

    @contextmanager
    def stage(self, node: str, **extra: Any) -> Iterator[None]:
        """包住一個 LangGraph node 的執行；RetryPolicy 重試會讓 attempt 遞增。"""
        attempt = self._stage_attempts.get(node, 0) + 1
        self._stage_attempts[node] = attempt
        start = time.monotonic()
        try:
            yield
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self.stages.append(
                {
                    "node": node,
                    "duration_ms": duration_ms,
                    "status": "failed",
                    "attempt": attempt,
                    **extra,
                }
            )
            self.error = {"node": node, "type": type(exc).__name__, "message": str(exc)[:500]}
            raise
        duration_ms = int((time.monotonic() - start) * 1000)
        self.stages.append(
            {"node": node, "duration_ms": duration_ms, "status": "ok", "attempt": attempt, **extra}
        )

    def record_llm_call(
        self,
        *,
        node: str,
        call: str,
        duration_ms: int,
        input_tokens: int = 0,
        output_tokens: int = 0,
        attempt: int = 1,
        segment_index: int | None = None,
    ) -> None:
        """每次 chat.ainvoke 記一筆；_invoke_writer 既有的合計 token_usage 不受影響。"""
        entry: dict[str, Any] = {
            "node": node,
            "call": call,
            "attempt": attempt,
            "duration_ms": duration_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        if segment_index is not None:
            entry["segment_index"] = segment_index
        self.llm_calls.append(entry)

    def set_research_summary(self, **fields: Any) -> None:
        """研究節點結束時呼叫；同一個 key 後到的呼叫覆寫先前值（非 append）。"""
        self.research.update(fields)

    def finalize(self, status: str, *, error: dict[str, Any] | None = None) -> None:
        self.status = status
        self.finished_at = datetime.now(UTC)
        if error is not None:
            self.error = error

    def gen_metrics(self) -> dict[str, Any]:
        wall_ms = int((time.monotonic() - self._start_monotonic) * 1000)
        queue_wait_ms: int | None = None
        if self.enqueued_at is not None:
            queue_wait_ms = int((self.started_at - self.enqueued_at).total_seconds() * 1000)
        total_in = sum(int(c.get("input_tokens", 0)) for c in self.llm_calls)
        total_out = sum(int(c.get("output_tokens", 0)) for c in self.llm_calls)
        return {
            "schema_version": _SCHEMA_VERSION,
            "status": self.status,
            "enqueued_at": self.enqueued_at.isoformat() if self.enqueued_at else None,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "queue_wait_ms": queue_wait_ms,
            "wall_ms": wall_ms,
            "stages": self.stages,
            "llm_calls": self.llm_calls,
            "totals": {
                "llm_call_count": len(self.llm_calls),
                "input_tokens": total_in,
                "output_tokens": total_out,
            },
            "error": self.error,
        }

    def research_metrics(self) -> dict[str, Any]:
        return dict(self.research)
