"""daily_podcast 單元測試。

純 Python mock 測試，不連 DB / 不打外部 API：
  - build_daily_messages 從 pick_daily_topics 選出的候選組 generate contract，
    候選不足（甚至 0 筆）原樣回傳，不補湊固定筆數。
  - enqueue_daily_batch 走 queue.send_daily_batch，並依新契約（-1／0／正整數）
    分流 log 與「是否標記候選 scheduled」。
  - queue.send_daily_batch 呼叫 SQL function（不直接迴圈 pgmq.send），且 0 筆
    時仍呼叫（不短路，marker 照樣要寫）。
  - worker._handle_control 收到 daily_podcast / channel_plan task 會分派到
    對應函式。

DB function 本身（marker claim + N send 原子性）需連 DB 的整合測試留給 Phase 5
真連驗，這層只驗 Python 端契約。
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from engine.pipeline import daily_batch


def _candidate(topic_id: str, channel_id: str, **overrides: Any) -> dict[str, Any]:
    """pick_daily_topics 回傳列的最小可用假資料（只含 _build_message 真的會讀的欄位）。"""
    base = {
        "topic_id": topic_id,
        "canonical_topic": "AI agents in the wild",
        "angle": "應用場景",
        "channel_id": channel_id,
        "topic": "tech",
        "topic_type": "evergreen",
        "length_tier": "medium",
        "cefr_level": "B1",
    }
    base.update(overrides)
    return base


# ── build_daily_messages ────────────────────────────────────────────────


async def test_build_daily_messages_empty_when_no_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候選庫存量不足或分數不到門檻 → pick_daily_topics 回空 list，
    build_daily_messages 原樣回空 list——「沒內容就不產」的第一道證據。"""

    async def fake_pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(daily_batch.channels, "pick_daily_topics", fake_pick_daily_topics)

    messages = await daily_batch.build_daily_messages("2026-07-25")
    assert messages == []


async def test_build_daily_messages_builds_full_contract_from_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候選轉成 generate message：既有欄位沿用現況 + 三個頻道新欄位齊全。"""

    async def fake_pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
        settings = daily_batch.get_settings()
        assert min_score == settings.channel_min_topic_score
        assert max_slots == settings.channel_daily_max_slots
        return [_candidate("topic-1", "chan-1")]

    async def fake_list_recent(channel_id: str, limit: int = 8) -> list[dict[str, Any]]:
        assert channel_id == "chan-1"
        # 一次查詢餵兩個用途：撈 avoid_facts 需要的較大範圍，series_context 再自己截短。
        assert limit == daily_batch._AVOID_FACTS_LOOKBACK
        return [
            {
                "slug": f"ep-{i}",
                "title": f"Past Episode {i}",
                "extracted_facts": [{"claim": f"fact-{i}"}],
            }
            for i in range(5)
        ]

    monkeypatch.setattr(daily_batch.channels, "pick_daily_topics", fake_pick_daily_topics)
    monkeypatch.setattr(daily_batch.channels, "list_recent_channel_episodes", fake_list_recent)

    messages = await daily_batch.build_daily_messages("2026-07-25")

    assert len(messages) == 1
    msg = messages[0]
    assert msg["big_topic"] == "tech"
    assert msg["canonical_topic"] == "AI agents in the wild"
    assert msg["angle"] == "應用場景"
    assert msg["topic_type"] == "evergreen"
    assert msg["length_tier"] == "medium"
    assert msg["cefr"] == "B1"
    assert msg["source"] == "daily_batch"
    assert msg["deliver_date"] == "2026-07-25"
    assert msg["user_ids"] == []
    assert msg["cluster_id"] is None
    # 頻道是同一主題連續出刊，相鄰集容易撞同一批事實，所以 avoid_facts 要帶滿。
    assert msg["avoid_facts"] == [f"fact-{i}" for i in range(5)]
    # 三個頻道新欄位（跟管線 agent 講好的契約，見 generate_job.py）
    assert msg["channel_id"] == "chan-1"
    assert msg["channel_topic_id"] == "topic-1"
    # series_context 只要最近 3 集標題，不跟著 avoid_facts 的較大範圍走。
    assert msg["series_context"] == [f"Past Episode {i}" for i in range(3)]


async def test_build_daily_messages_tolerates_null_extracted_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """episodes.extracted_facts 可為 NULL（seed / 手動匯入的集沒跑過抽取）。

    collect_avoid_facts 要能吃 None 不炸——否則頻道裡混進一集舊資料，
    整批 daily batch 就會在組訊息階段掛掉。
    """

    async def fake_pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
        return [_candidate("topic-1", "chan-1")]

    async def fake_list_recent(channel_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return [
            {"slug": "ep-0", "title": "No Facts", "extracted_facts": None},
            {"slug": "ep-1", "title": "Has Facts", "extracted_facts": [{"claim": "fact-1"}]},
        ]

    monkeypatch.setattr(daily_batch.channels, "pick_daily_topics", fake_pick_daily_topics)
    monkeypatch.setattr(daily_batch.channels, "list_recent_channel_episodes", fake_list_recent)

    messages = await daily_batch.build_daily_messages("2026-07-25")

    assert messages[0]["avoid_facts"] == ["fact-1"]
    assert messages[0]["series_context"] == ["No Facts", "Has Facts"]


async def test_build_daily_messages_allows_more_than_2_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拿掉「必須剛好 2 筆」的舊斷言：候選筆數完全交給 pick_daily_topics 決定。"""

    async def fake_pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
        return [_candidate(f"topic-{i}", f"chan-{i}") for i in range(4)]

    async def fake_list_recent(channel_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(daily_batch.channels, "pick_daily_topics", fake_pick_daily_topics)
    monkeypatch.setattr(daily_batch.channels, "list_recent_channel_episodes", fake_list_recent)

    messages = await daily_batch.build_daily_messages("2026-07-25")
    assert len(messages) == 4


# ── enqueue_daily_batch：新契約 -1／0／正整數 ────────────────────────────


async def test_enqueue_daily_batch_0_candidates_still_calls_sql_function(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """0 候選 → build_daily_messages 回空 list；enqueue_daily_batch 仍呼叫
    send_daily_batch（寫 marker 記錄「今天評估過」），回傳 0，不可短路 return。"""
    captured: dict[str, Any] = {}

    async def fake_pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
        return []

    async def fake_send_daily_batch(deliver_date: str, bodies: list[dict[str, Any]]) -> int:
        captured["called"] = True
        captured["bodies"] = bodies
        return 0

    monkeypatch.setattr(daily_batch.channels, "pick_daily_topics", fake_pick_daily_topics)
    monkeypatch.setattr(daily_batch.queue, "send_daily_batch", fake_send_daily_batch)

    with caplog.at_level(logging.INFO):
        n = await daily_batch.enqueue_daily_batch("2026-07-25")

    assert n == 0
    assert captured["called"] is True  # SQL function 真的被呼叫，不是短路 return
    assert captured["bodies"] == []
    assert "今天沒有合格候選，不產出" in caplog.text


async def test_enqueue_daily_batch_returns_minus_1_on_duplicate_and_skips_marking(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """-1＝該日期已被其他 worker claim；這批候選完全沒被標記 scheduled
    （避免 duplicate control 把還沒真的送出的候選錯誤標成排程中）。"""
    marked: list[str] = []

    async def fake_pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
        return [_candidate("topic-1", "chan-1")]

    async def fake_list_recent(channel_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    async def fake_send_daily_batch(deliver_date: str, bodies: list[dict[str, Any]]) -> int:
        return -1

    async def fake_update_topic_status(
        topic_id: str, status: str, *, episode_id: str | None = None
    ) -> bool:
        marked.append(topic_id)
        return True

    monkeypatch.setattr(daily_batch.channels, "pick_daily_topics", fake_pick_daily_topics)
    monkeypatch.setattr(daily_batch.channels, "list_recent_channel_episodes", fake_list_recent)
    monkeypatch.setattr(daily_batch.queue, "send_daily_batch", fake_send_daily_batch)
    monkeypatch.setattr(daily_batch.channels, "update_topic_status", fake_update_topic_status)

    with caplog.at_level(logging.INFO):
        n = await daily_batch.enqueue_daily_batch("2026-07-25")

    assert n == -1
    assert marked == []
    assert "已被其他 worker 處理" in caplog.text


async def test_enqueue_daily_batch_positive_marks_topics_scheduled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """正整數＝正常送出；每筆候選都要回填 update_topic_status(..., 'scheduled')。"""
    marked: list[tuple[str, str]] = []

    async def fake_pick_daily_topics(*, min_score: float, max_slots: int) -> list[dict[str, Any]]:
        return [_candidate("topic-1", "chan-1"), _candidate("topic-2", "chan-2")]

    async def fake_list_recent(channel_id: str, limit: int = 8) -> list[dict[str, Any]]:
        return []

    async def fake_send_daily_batch(deliver_date: str, bodies: list[dict[str, Any]]) -> int:
        return len(bodies)

    async def fake_update_topic_status(
        topic_id: str, status: str, *, episode_id: str | None = None
    ) -> bool:
        marked.append((topic_id, status))
        return True

    monkeypatch.setattr(daily_batch.channels, "pick_daily_topics", fake_pick_daily_topics)
    monkeypatch.setattr(daily_batch.channels, "list_recent_channel_episodes", fake_list_recent)
    monkeypatch.setattr(daily_batch.queue, "send_daily_batch", fake_send_daily_batch)
    monkeypatch.setattr(daily_batch.channels, "update_topic_status", fake_update_topic_status)

    with caplog.at_level(logging.INFO):
        n = await daily_batch.enqueue_daily_batch("2026-07-25")

    assert n == 2
    assert marked == [("topic-1", "scheduled"), ("topic-2", "scheduled")]
    assert "enqueue 完成" in caplog.text


# ── worker._handle_control 分派 ──────────────────────────────────────────


async def test_worker_handle_control_dispatches_daily_podcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 收到 {task:'daily_podcast', date:...} 會走 enqueue_daily_batch。"""
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


async def test_worker_handle_control_dispatches_channel_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 收到 {task:'channel_plan'} 沒帶 channel_id 時走 plan_channels(channel_id=None)
    （cron 觸發＝跑全部頻道）。"""
    from engine import worker

    captured: dict[str, Any] = {}

    async def fake_plan_channels(*, channel_id: str | None = None) -> int:
        captured["channel_id"] = channel_id
        return 3

    monkeypatch.setattr(worker, "plan_channels", fake_plan_channels)

    await worker._handle_control({"task": "channel_plan", "date": "2026-07-25"})

    assert captured["channel_id"] is None


async def test_worker_handle_control_channel_plan_passes_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """admin 手動觸發帶 channel_id 時原樣傳入 plan_channels（只跑該頻道）。"""
    from engine import worker

    captured: dict[str, Any] = {}

    async def fake_plan_channels(*, channel_id: str | None = None) -> int:
        captured["channel_id"] = channel_id
        return 1

    monkeypatch.setattr(worker, "plan_channels", fake_plan_channels)

    await worker._handle_control({"task": "channel_plan", "channel_id": "chan-42"})

    assert captured["channel_id"] == "chan-42"


# ── queue.send_daily_batch ───────────────────────────────────────────────


def _fake_connection_factory(captured_sql: list[str], sent_count: int) -> Any:
    """組一個可 monkeypatch 進 shared.db.queue.connection 的假 connection 工廠。

    只捕捉 execute() 收到的 SQL 文字，回放固定的 sent_count 給 fetchone()。
    """

    class _FakeCursor:
        def __init__(self) -> None:
            self._row = {"sent_count": sent_count}

        async def __aenter__(self) -> _FakeCursor:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        async def execute(self, sql: str, params: Any = None) -> None:
            captured_sql.append(sql)

        async def fetchone(self) -> dict[str, Any]:
            return self._row

    class _FakeConn:
        async def __aenter__(self) -> _FakeConn:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

        def cursor(self, **_: Any) -> _FakeCursor:
            return _FakeCursor()

    return lambda: _FakeConn()


async def test_send_daily_batch_uses_sql_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """queue.send_daily_batch 必須走 SQL function，不能直接迴圈 pgmq.send。"""
    from shared.db import queue as qmod

    captured_sql: list[str] = []
    monkeypatch.setattr(qmod, "connection", _fake_connection_factory(captured_sql, sent_count=2))

    n = await qmod.send_daily_batch("2026-07-25", [{"big_topic": "tech"}, {"big_topic": "biz"}])

    assert n == 2
    assert len(captured_sql) == 1
    # 必須呼叫 SQL function；不能是直接 pgmq.send 拼字串
    assert "enqueue_daily_podcast_batch" in captured_sql[0]
    assert "pgmq.send" not in captured_sql[0]


async def test_send_daily_batch_allows_empty_list_and_still_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 筆時仍要呼叫 SQL function（寫 marker），不可在 Python 端短路 return。"""
    from shared.db import queue as qmod

    captured_sql: list[str] = []
    monkeypatch.setattr(qmod, "connection", _fake_connection_factory(captured_sql, sent_count=0))

    n = await qmod.send_daily_batch("2026-07-25", [])

    assert n == 0
    assert len(captured_sql) == 1  # 真的發了一次查詢，不是短路 return


async def test_send_daily_batch_rejects_more_than_10_bodies() -> None:
    """上限對齊 migration 0022 的 DB 端檢查：Python 層先擋，不必等 DB round-trip。"""
    from shared.db import queue as qmod

    with pytest.raises(ValueError):
        await qmod.send_daily_batch("2026-07-25", [{"i": i} for i in range(11)])
