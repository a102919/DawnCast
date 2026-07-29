"""channel_plan（選題 planner）單元測試。

純 Python mock 測試，不連 DB / 不打外部 API：
  - backlog 已達 channel_backlog_target 時完全不呼叫 LLM（成本控制的關鍵）
  - LLM 回傳非法 JSON 時重試耗盡後回 0，不拋例外（不可讓整批選題炸掉）
  - topic_type='news' 會抓外部來源、'evergreen' 不會
  - happy path：LLM 候選正確寫進 insert_channel_topics
  - channel_id 指定時只跑該頻道；找不到回 0
  - 單一頻道選題失敗不影響同批其他頻道

沿用既有測試的 FakeChatModel（langgraph_pod/chat.py）模式：monkeypatch
channel_plan 模組內的 make_langchain_chat / make_source_provider 與
shared.db.channels 的各個函式，不連真 DB。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from engine.pipeline import channel_plan
from engine.pipeline.langgraph_pod.chat import FakeChatModel
from shared.models import SourceSnippet


def _channel(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "chan-1",
        "slug": "ai-daily",
        "theme_prompt": "每天一則 AI 應用案例",
        "topic": "tech",
        "topic_type": "evergreen",
        "length_tier": "medium",
        "cefr_level": "B1",
    }
    base.update(overrides)
    return base


def _candidates_json(n: int = 3) -> str:
    candidates = [
        {
            "canonical_topic": f"Topic {i}",
            "angle": "定義",
            "rationale": "理由",
            "score": 0.8,
            "continues_episode_slug": None,
        }
        for i in range(n)
    ]
    return json.dumps({"candidates": candidates})


def _stub_channel_repo(
    monkeypatch: pytest.MonkeyPatch,
    *,
    channels_list: list[dict[str, Any]] | None = None,
    get_channel_result: dict[str, Any] | None = None,
    backlog: int | dict[str, int] = 0,
    recent_episodes: list[dict[str, Any]] | None = None,
    existing_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, list[Any]]:
    """monkeypatch channel_plan.channels 的讀寫方法，回傳捕捉 insert 呼叫的容器。

    backlog 可以是單一 int（所有頻道共用）或 {channel_id: int} 的對照表（依頻道
    分流測試「有些頻道存量夠、有些不夠」的情境）。
    """
    calls: dict[str, list[Any]] = {"insert": [], "count_candidates": []}

    async def fake_list_channels(*, status: str | None = None) -> list[dict[str, Any]]:
        return channels_list or []

    async def fake_get_channel(channel_id: str) -> dict[str, Any] | None:
        return get_channel_result

    async def fake_count_candidates(channel_id: str) -> int:
        calls["count_candidates"].append(channel_id)
        if isinstance(backlog, dict):
            return backlog[channel_id]
        return backlog

    async def fake_list_recent_channel_episodes(
        channel_id: str, limit: int = 8
    ) -> list[dict[str, Any]]:
        return recent_episodes or []

    async def fake_list_channel_topics(
        channel_id: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        return existing_candidates or []

    async def fake_insert_channel_topics(
        channel_id: str, candidates: list[dict[str, Any]]
    ) -> int:
        calls["insert"].append((channel_id, candidates))
        return len(candidates)

    monkeypatch.setattr(channel_plan.channels, "list_channels", fake_list_channels)
    monkeypatch.setattr(channel_plan.channels, "get_channel", fake_get_channel)
    monkeypatch.setattr(channel_plan.channels, "count_candidates", fake_count_candidates)
    monkeypatch.setattr(
        channel_plan.channels,
        "list_recent_channel_episodes",
        fake_list_recent_channel_episodes,
    )
    monkeypatch.setattr(channel_plan.channels, "list_channel_topics", fake_list_channel_topics)
    monkeypatch.setattr(channel_plan.channels, "insert_channel_topics", fake_insert_channel_topics)
    return calls


# ── backlog 已達標：不呼叫 LLM ───────────────────────────────────────────


async def test_plan_channels_skips_llm_when_backlog_met(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """count_candidates >= channel_backlog_target 時完全不呼叫 LLM（成本控制關鍵）。"""
    channel = _channel()
    target = channel_plan.get_settings().channel_backlog_target
    _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=target)

    fake_chat = FakeChatModel(responses=[_candidates_json()])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels()

    assert n == 0
    assert fake_chat._call_count == 0


# ── LLM 回傳非法 JSON：重試後回 0，不拋例外 ─────────────────────────────


async def test_plan_channels_invalid_json_retries_then_returns_0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 一直回非法 JSON：重試耗盡後回 0，不拋例外（選題失敗不該炸整批）。"""
    channel = _channel()
    _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=0)

    fake_chat = FakeChatModel(responses=["not json at all"])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels()  # 不應拋例外

    assert n == 0
    assert fake_chat._call_count == channel_plan._MAX_CANDIDATE_RETRIES + 1


async def test_plan_channels_invalid_angle_counts_as_parse_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """angle 不在 ANGLES taxonomy 裡也是一種解析失敗（Pydantic validator 擋下）。"""
    channel = _channel()
    _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=0)

    bad_json = json.dumps(
        {
            "candidates": [
                {
                    "canonical_topic": "Topic X",
                    "angle": "不存在的角度",
                    "rationale": "理由",
                    "score": 0.8,
                    "continues_episode_slug": None,
                }
            ]
        }
    )
    fake_chat = FakeChatModel(responses=[bad_json])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels()

    assert n == 0
    assert fake_chat._call_count == channel_plan._MAX_CANDIDATE_RETRIES + 1


# ── news/product 抓外部來源、evergreen/skill 不抓 ───────────────────────


async def test_plan_channels_evergreen_does_not_fetch_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """evergreen 頻道不呼叫 make_source_provider（Wikipedia 恆為真，不需要判斷）。"""
    channel = _channel(topic_type="evergreen")
    _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=0)

    fetch_calls: list[str] = []

    def fake_make_source_provider(topic_type: str, settings: Any = None) -> Any:
        fetch_calls.append(topic_type)
        return None

    monkeypatch.setattr(channel_plan, "make_source_provider", fake_make_source_provider)
    fake_chat = FakeChatModel(responses=[_candidates_json()])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    await channel_plan.plan_channels()

    assert fetch_calls == []


async def test_plan_channels_news_fetches_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """news 頻道會呼叫 source provider 抓 theme_prompt 的近期素材。"""
    channel = _channel(topic_type="news", theme_prompt="今日科技新聞")
    _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=0)

    fetch_queries: list[str] = []

    class _FakeProvider:
        async def fetch(self, query: str) -> list[SourceSnippet]:
            fetch_queries.append(query)
            return [SourceSnippet(id="s1", title="News", url="https://x", text="近期發生的事")]

        async def aclose(self) -> None:
            pass

    def fake_make_source_provider(topic_type: str, settings: Any = None) -> Any:
        assert topic_type == "news"
        return _FakeProvider()

    monkeypatch.setattr(channel_plan, "make_source_provider", fake_make_source_provider)
    fake_chat = FakeChatModel(responses=[_candidates_json()])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    await channel_plan.plan_channels()

    assert fetch_queries == ["今日科技新聞"]


async def test_plan_channels_source_fetch_failure_degrades_to_no_snippets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SourceFetchError 降級成空素材，不擋選題（跟 gather_evidence_node 同精神）。"""
    channel = _channel(topic_type="news")
    _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=0)

    closed = []

    class _FailingProvider:
        async def fetch(self, query: str) -> list[SourceSnippet]:
            from shared.errors import SourceFetchError

            raise SourceFetchError("mock 抓取失敗")

        async def aclose(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        channel_plan, "make_source_provider", lambda topic_type, settings=None: _FailingProvider()
    )
    fake_chat = FakeChatModel(responses=[_candidates_json()])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels()

    assert n == 3  # 抓取失敗仍照樣產出候選，不會整個中斷
    assert closed == [True]  # aclose 一定要被呼叫（finally 保證）


# ── happy path：LLM 候選正確寫進 insert_channel_topics ──────────────────


async def test_plan_channels_inserts_llm_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _channel()
    calls = _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=0)
    fake_chat = FakeChatModel(responses=[_candidates_json(3)])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels()

    assert n == 3
    assert len(calls["insert"]) == 1
    channel_id, candidates = calls["insert"][0]
    assert channel_id == "chan-1"
    assert len(candidates) == 3
    assert candidates[0]["angle"] == "定義"
    assert candidates[0]["score"] == 0.8


async def test_plan_channels_continuity_note_appended_to_rationale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """continues_episode_slug 有值時要附註進 rationale，不能被靜默丟棄。"""
    channel = _channel()
    calls = _stub_channel_repo(monkeypatch, channels_list=[channel], backlog=0)

    candidate_json = json.dumps(
        {
            "candidates": [
                {
                    "canonical_topic": "Topic Follow-up",
                    "angle": "對比",
                    "rationale": "延續上一集沒講完的部分",
                    "score": 0.9,
                    "continues_episode_slug": "prior-episode-slug",
                }
            ]
        }
    )
    fake_chat = FakeChatModel(responses=[candidate_json])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    await channel_plan.plan_channels()

    _, candidates = calls["insert"][0]
    assert "prior-episode-slug" in candidates[0]["rationale"]


# ── channel_id 指定範圍 ──────────────────────────────────────────────────


async def test_plan_channels_with_channel_id_only_targets_that_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """channel_id 有值時走 get_channel，不呼叫 list_channels（不限 status）。"""
    channel = _channel()
    list_channels_called = False

    async def fake_list_channels(*, status: str | None = None) -> list[dict[str, Any]]:
        nonlocal list_channels_called
        list_channels_called = True
        return []

    calls = _stub_channel_repo(monkeypatch, get_channel_result=channel, backlog=0)
    monkeypatch.setattr(channel_plan.channels, "list_channels", fake_list_channels)
    fake_chat = FakeChatModel(responses=[_candidates_json(3)])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels(channel_id="chan-1")

    assert n == 3
    assert list_channels_called is False
    assert calls["insert"][0][0] == "chan-1"


async def test_plan_channels_channel_id_not_found_returns_0_without_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_channel_repo(monkeypatch, get_channel_result=None)
    fake_chat = FakeChatModel(responses=[_candidates_json()])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels(channel_id="missing")

    assert n == 0
    assert fake_chat._call_count == 0


# ── 單一頻道失敗不拖垮其他頻道 ───────────────────────────────────────────


async def test_plan_channels_one_channel_failure_does_not_block_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一個頻道的選題流程炸掉（例如資料庫例外）只跳過該頻道，其他頻道照跑。"""
    ok_channel = _channel(id="chan-ok", slug="ok")
    bad_channel = _channel(id="chan-bad", slug="bad")

    calls = _stub_channel_repo(
        monkeypatch, channels_list=[bad_channel, ok_channel], backlog=0
    )

    async def fake_count_candidates(channel_id: str) -> int:
        if channel_id == "chan-bad":
            raise RuntimeError("boom")
        return 0

    monkeypatch.setattr(channel_plan.channels, "count_candidates", fake_count_candidates)

    fake_chat = FakeChatModel(responses=[_candidates_json(2)])
    monkeypatch.setattr(channel_plan, "make_langchain_chat", lambda *a, **kw: fake_chat)

    n = await channel_plan.plan_channels()

    assert n == 2  # 只有 ok_channel 成功插入
    assert len(calls["insert"]) == 1
    assert calls["insert"][0][0] == "chan-ok"
