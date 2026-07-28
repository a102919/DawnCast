"""daily_podcast 單元測試。

純 Python mock 測試，不連 DB / 不打外部 API：
  - build_daily_messages 產 2 個完整 generate contract
  - enqueue_daily_batch 走 queue.send_daily_batch 並對回傳值 log
  - queue.send_daily_batch 呼叫 SQL function（不直接迴圈 pgmq.send）
  - worker._handle_control 收到 daily_podcast task 會走 enqueue_daily_batch

DB function 本身（marker claim + 2 send 原子性）需連 DB 的整合測試留給
Phase 5 真連驗，這層只驗 Python 端契約。
"""

from __future__ import annotations

from typing import Any

import pytest

from engine.pipeline import daily_batch


def test_build_daily_messages_produces_2_with_required_fields() -> None:
    """2 個 slot，每個欄位齊全。"""
    messages = daily_batch.build_daily_messages("2026-07-25")

    assert len(messages) == 2

    expected_topics = ["tech", "business"]
    expected_canonicals = [
        "AI agents at work",
        "Compound interest in everyday decisions",
    ]
    expected_angles = ["定義", "應用場景"]

    for i, msg in enumerate(messages):
        assert msg["big_topic"] == expected_topics[i]
        assert msg["canonical_topic"] == expected_canonicals[i]
        assert msg["angle"] == expected_angles[i]
        assert msg["topic_type"] == "evergreen"
        assert msg["length_tier"] == "medium"
        assert msg["cefr"] == "B1"
        assert msg["source"] == "daily_batch"
        assert msg["deliver_date"] == "2026-07-25"
        assert msg["user_ids"] == []
        assert msg["cluster_id"] is None
        assert msg["avoid_facts"] == []


async def test_enqueue_daily_batch_calls_send_daily_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """happy path：build 完 2 筆 → 呼叫 SQL function → 回傳 2。"""
    captured: dict[str, Any] = {}

    async def fake_send_daily_batch(deliver_date: str, bodies: list[dict[str, Any]]) -> int:
        captured["deliver_date"] = deliver_date
        captured["bodies"] = bodies
        return 2

    monkeypatch.setattr(daily_batch.queue, "send_daily_batch", fake_send_daily_batch)

    n = await daily_batch.enqueue_daily_batch("2026-07-25")

    assert n == 2
    assert captured["deliver_date"] == "2026-07-25"
    assert len(captured["bodies"]) == 2
    assert captured["bodies"][0]["source"] == "daily_batch"


async def test_enqueue_daily_batch_returns_0_on_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB function 回 0 → 視為已 claim；enqueue 函式也回 0（不 raise）。"""
    async def fake_send_daily_batch(deliver_date: str, bodies: list[dict[str, Any]]) -> int:
        return 0

    monkeypatch.setattr(daily_batch.queue, "send_daily_batch", fake_send_daily_batch)

    n = await daily_batch.enqueue_daily_batch("2026-07-25")
    assert n == 0


async def test_enqueue_daily_batch_returns_unexpected_count_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB function 回傳 1 不該發生；函式仍回傳該值（不 raise），由 worker log 標紅。"""
    async def fake_send_daily_batch(deliver_date: str, bodies: list[dict[str, Any]]) -> int:
        return 1

    monkeypatch.setattr(daily_batch.queue, "send_daily_batch", fake_send_daily_batch)

    n = await daily_batch.enqueue_daily_batch("2026-07-25")
    assert n == 1


async def test_worker_handle_control_dispatches_daily_podcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 收到 {task:'daily_podcast', date:'2026-07-25'} 會走 enqueue_daily_batch。"""
    from engine import worker

    captured: dict[str, Any] = {}

    async def fake_enqueue_daily_batch(deliver_date: str) -> int:
        captured["deliver_date"] = deliver_date
        return 2

    monkeypatch.setattr(worker, "enqueue_daily_batch", fake_enqueue_daily_batch)

    await worker._handle_control({"task": "daily_podcast", "date": "2026-07-25"})

    assert captured["deliver_date"] == "2026-07-25"


async def test_worker_handle_control_daily_podcast_uses_anchor_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沒帶 date 時用 app timezone 當天（與既有 collect_open / orchestrate 一致）。"""
    from engine import worker

    captured: dict[str, Any] = {}

    async def fake_enqueue_daily_batch(deliver_date: str) -> int:
        captured["deliver_date"] = deliver_date
        return 2

    monkeypatch.setattr(worker, "enqueue_daily_batch", fake_enqueue_daily_batch)

    await worker._handle_control({"task": "daily_podcast"})

    # anchor 格式 YYYY-MM-DD；不驗具體值（時區決定），只驗「有東西進去」
    assert isinstance(captured["deliver_date"], str)
    assert len(captured["deliver_date"]) == 10


async def test_send_daily_batch_uses_sql_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """queue.send_daily_batch 必須走 SQL function，不能直接迴圈 pgmq.send。"""
    from shared.db import queue as qmod

    captured_sql: list[str] = []

    class _FakeConn:
        async def __aenter__(self) -> _FakeConn:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        def cursor(self, **_: Any) -> _FakeCursor:
            return _FakeCursor(captured_sql)

    class _FakeCursor:
        def __init__(self, sink: list[str]) -> None:
            self._sink = sink
            self._row = {"sent_count": 2}

        async def __aenter__(self) -> _FakeCursor:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def execute(self, sql: str, params: Any = None) -> None:
            self._sink.append(sql)

        async def fetchone(self) -> dict[str, Any]:
            return self._row

    monkeypatch.setattr(qmod, "connection", lambda: _FakeConn())

    bodies = daily_batch.build_daily_messages("2026-07-25")
    n = await qmod.send_daily_batch("2026-07-25", bodies)

    assert n == 2
    assert len(captured_sql) == 1
    # 必須呼叫 SQL function；不能是直接 pgmq.send 拼字串
    assert "enqueue_daily_podcast_batch" in captured_sql[0]
    assert "pgmq.send" not in captured_sql[0]
