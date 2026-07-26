"""MetricsCollector 單元測試：計時、例外路徑、llm call 累加、序列化形狀。"""

from __future__ import annotations

import pytest

from engine.pipeline.langgraph_pod.metrics import MetricsCollector


def test_stage_records_duration_and_status_ok() -> None:
    collector = MetricsCollector(idempotency_key="idem-1")
    with collector.stage("decompose_research"):
        pass
    assert len(collector.stages) == 1
    stage = collector.stages[0]
    assert stage["node"] == "decompose_research"
    assert stage["status"] == "ok"
    assert stage["attempt"] == 1
    assert stage["duration_ms"] >= 0


def test_stage_records_failure_and_sets_error() -> None:
    collector = MetricsCollector(idempotency_key="idem-1")
    with pytest.raises(ValueError), collector.stage("gather_evidence"):
        raise ValueError("boom")
    assert len(collector.stages) == 1
    stage = collector.stages[0]
    assert stage["status"] == "failed"
    assert collector.error is not None
    assert collector.error["node"] == "gather_evidence"
    assert collector.error["type"] == "ValueError"
    assert "boom" in collector.error["message"]


def test_stage_attempt_increments_on_retry() -> None:
    collector = MetricsCollector(idempotency_key="idem-1")
    with collector.stage("write_script"):
        pass
    with collector.stage("write_script"):
        pass
    attempts = [s["attempt"] for s in collector.stages]
    assert attempts == [1, 2]


def test_record_llm_call_accumulates_totals() -> None:
    collector = MetricsCollector(idempotency_key="idem-1")
    collector.record_llm_call(
        node="write_script", call="outline", duration_ms=100, input_tokens=10, output_tokens=5
    )
    collector.record_llm_call(
        node="write_script",
        call="segment",
        duration_ms=200,
        input_tokens=20,
        output_tokens=15,
        segment_index=0,
    )
    metrics = collector.gen_metrics()
    assert metrics["totals"]["llm_call_count"] == 2
    assert metrics["totals"]["input_tokens"] == 30
    assert metrics["totals"]["output_tokens"] == 20
    assert metrics["llm_calls"][1]["segment_index"] == 0


def test_set_research_summary_merges_not_replaces() -> None:
    collector = MetricsCollector(idempotency_key="idem-1")
    collector.set_research_summary(questions_count=3)
    collector.set_research_summary(source_count=5)
    research = collector.research_metrics()
    assert research == {"questions_count": 3, "source_count": 5}


def test_queue_wait_ms_computed_from_enqueued_at() -> None:
    from datetime import UTC, datetime, timedelta

    enqueued = datetime.now(UTC) - timedelta(seconds=5)
    collector = MetricsCollector(idempotency_key="idem-1", enqueued_at=enqueued)
    metrics = collector.gen_metrics()
    assert metrics["queue_wait_ms"] >= 4900  # 允許測試執行些微誤差


def test_queue_wait_ms_none_without_enqueued_at() -> None:
    collector = MetricsCollector(idempotency_key="idem-1")
    metrics = collector.gen_metrics()
    assert metrics["queue_wait_ms"] is None


def test_finalize_sets_status_and_finished_at() -> None:
    collector = MetricsCollector(idempotency_key="idem-1")
    assert collector.finished_at is None
    collector.finalize("success")
    assert collector.status == "success"
    assert collector.finished_at is not None
    metrics = collector.gen_metrics()
    assert metrics["status"] == "success"
    assert metrics["finished_at"] is not None


def test_gen_metrics_is_json_serializable() -> None:
    import json

    collector = MetricsCollector(idempotency_key="idem-1")
    with collector.stage("write_script"):
        collector.record_llm_call(
            node="write_script", call="outline", duration_ms=1, input_tokens=1, output_tokens=1
        )
    collector.finalize("success")
    json.dumps(collector.gen_metrics())  # 不 raise 即通過
    json.dumps(collector.research_metrics())
