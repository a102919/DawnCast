"""LangGraph pod 的專屬測試（純 mock，不連 DB / R2 / LLM）。

涵蓋場景：
  1. 基礎 happy path
  2. judge 不及格 → 觸發 rewrite → 第二次及格
  3. judge 持續不及格 → 觸發 N 次後放行（cap 機制）
  4. 冪等鍵：同 (deliver_date, big_topic, angle) 第二次呼叫 already_rendered=True
  5. R2 put_object 失敗 → 走 local fallback，key 全 None
  6. rate-limit → 沒 failover chat 時直接 END（degrade 行為）
  7. rate-limit → 有 failover chat 時切到 failover 引擎
  8. MiniMaxChatModel 構造契約（不真實打 API）
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from engine.pipeline.langgraph_pod import run_pod
from engine.pipeline.langgraph_pod.chat import FakeChatModel, make_langchain_chat
from engine.pipeline.langgraph_pod.mock import (
    MockR2,
    MockRenderer,
    get_mocks,
    make_mock_workdir,
)
from shared.config import get_settings
from shared.errors import RateLimitError
from shared.models import ScriptJSON

# ── 共用 fixture ─────────────────────────────────────────────


def _script_json(*, format: str = "dialogue") -> str:
    """合法 ScriptJSON 字串（≥8 行）。dialogue：雙主持人齊備；monologue：單一 Nova。"""
    facts = [
        {"claim": "f1", "source_ids": []},
        {"claim": "f2", "source_ids": []},
        {"claim": "f3", "source_ids": []},
    ]
    if format == "monologue":
        script = [{"speaker": "Nova", "text": f"line {i}", "zh": f"第{i}行"} for i in range(8)]
    else:
        speakers = ["Alex", "Sarah"]
        script = [
            {"speaker": speakers[i % 2], "text": f"line {i}", "zh": f"第{i}行"} for i in range(8)
        ]
    return json.dumps(
        {
            "topic": "Quantum",
            "topic_zh": "量子力學入門",
            "extracted_facts": facts,
            "target_vocab": [{"word": "quantum", "explanation": "tiny unit"}],
            "format": format,
            "script": script,
        }
    )


def _judge_json(score: float, feedback: list[str] | None = None) -> str:
    """五軸給同一分數（測試不關心軸間差異，只關心過/不過門檻）。"""
    return json.dumps(
        {
            "hook_strength": score,
            "informativeness": score,
            "pacing": score,
            "chemistry": score,
            "groundedness": score,
            "feedback": feedback or [],
        }
    )


def _make_passing_chat() -> FakeChatModel:
    return FakeChatModel(
        responses=[_script_json()],
        judge_responses=[_judge_json(0.8)],  # 全過 0.6 門檻
    )


def _body() -> dict[str, Any]:
    return {
        "big_topic": "科技",
        "canonical_topic": "量子力學",
        "angle": "定義",
        "topic_type": "evergreen",
        "deliver_date": "2026-07-14",
        "user_ids": ["u1", "u2"],
    }


# ── 1. happy path ────────────────────────────────────────────


async def test_pod_happy_path() -> None:
    chat = _make_passing_chat()
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert eid
    assert len(repo.deliveries) == 2  # u1, u2
    assert len(r2.objects) == 2  # mp3 / srt
    assert chat._call_count == 2  # writer + judge


# ── 2. judge 不及格 → rewrite → 及格 ──────────────────────


async def test_judge_triggers_rewrite_then_passes() -> None:
    chat = FakeChatModel(
        responses=[_script_json(), _script_json()],
        judge_responses=[
            _judge_json(0.4, ["add hook", "more chemistry"]),  # 不及格
            _judge_json(0.8),  # 第二次及格
        ],
    )
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert eid
    # writer x2（重寫一次）+ judge x2
    assert chat._call_count == 4
    # judge_feedback 應該把第一輪的 feedback 帶進 writer 第二輪的 prompt
    # 這裡只驗收 episode 確實產出，feedback 是否被 prompt 採納看 node 端人工 trace


# ── 3. judge 持續不及格 → cap 後放行 ─────────────────────


async def test_judge_rewrite_cap_respected() -> None:
    """judge 永遠給爛分 → max_rewrite_iterations 次後放行，不無限循環。"""
    # 給 3 次 writer + 3 次 judge（cap=2 → 2 次重寫後第 3 次 judge 不及格仍放行）
    chat = FakeChatModel(
        responses=[_script_json()] * 4,
        judge_responses=[_judge_json(0.3, ["bad"])] * 4,
    )
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert eid
    # 1 初始 + 2 rewrite = 3 writer + 3 judge = 6 calls
    assert chat._call_count == 6
    # 不會到 8（不會無限循環）


# ── 4. 冪等鍵：同 body 第二次呼叫 already_rendered=True ─


async def test_idempotent_second_call_skips_render() -> None:
    chat = _make_passing_chat()
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid1 = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    # 第一次：2 個 R2 物件 (mp3 + srt)
    assert len(r2.objects) == 2
    # 第二次同 body：already_rendered=True → 跳過 render + upload
    eid2 = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert eid1 == eid2
    # 第二次沒新增 R2 物件
    assert len(r2.objects) == 2
    # MockRepo insert_delivery 模擬 ON CONFLICT DO NOTHING → 同 (user, ep, date)
    # 第二次不會新增。所以最終只有 2 筆（u1, u2 各一）。
    assert len(repo.deliveries) == 2


# ── 5. R2 失敗 → local fallback，R2 key 全 None ──────────


async def test_r2_failure_falls_back_to_local_keys_null() -> None:
    chat = _make_passing_chat()
    repo, _, queue = get_mocks(reset=True)
    r2 = MockR2()
    r2.fail_put = True
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert eid
    ep = repo.get_episode(eid)
    assert ep is not None
    assert ep.audio_key is None
    assert ep.srt_key is None
    # 仍交付
    assert len(repo.deliveries) == 2


# ── 5b. R2 失敗 + 本機 fallback 也失敗 → DELETE row + raise ────


async def test_r2_failure_with_no_local_fallback_deletes_row() -> None:
    """媒體雙重失敗不能留殭屍 row：先 DELETE 再 raise。

    觸發條件：local_media_dir 沒設 → safe_local_fallback 不寫檔 →
    update_episode_keys_node 偵測 storage_failed + 無本機 mp3 → DELETE + raise。
    """
    chat = _make_passing_chat()
    repo, r2, queue = get_mocks(reset=True)
    r2.fail_put = True
    renderer = MockRenderer(make_mock_workdir())

    # local_media_dir=None → 沒有任何本機 fallback 機會
    settings = get_settings().model_copy(update={"local_media_dir": None})

    with pytest.raises(RuntimeError, match="雙重失敗"):
        await run_pod(
            _body(),
            chat=chat,
            repo=repo,
            r2=r2,
            queue=queue,
            renderer=renderer,
            settings=settings,
        )

    # row 被補償清掉、沒交付
    assert len(repo.episodes) == 0
    assert len(repo.by_idem) == 0
    assert repo.deliveries == []


# ── 6. rate-limit + 無 failover → degrade（raise RateLimitError）


async def test_rate_limit_degrade_raises_without_failover() -> None:
    """primary 撞 429、沒給 chat_failover → run_pod 應 raise RateLimitError。"""
    chat = FakeChatModel(responses=[RateLimitError("429 mock")])
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    with pytest.raises(RateLimitError):
        await run_pod(
            _body(),
            chat=chat,
            repo=repo,
            r2=r2,
            queue=queue,
            renderer=renderer,
        )
    # 沒落庫、沒交付
    assert repo.deliveries == []
    assert len(repo.episodes) == 0


# ── 7. rate-limit + 有 failover → 切到 chat_failover ──────


async def test_rate_limit_triggers_failover_chat() -> None:
    chat = FakeChatModel(responses=[RateLimitError("429 primary")])
    chat_failover = FakeChatModel(
        responses=[_script_json()],
        judge_responses=[_judge_json(0.8)],
    )
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    # failover_mode=failover 才會啟用 conditional edge 切到 chat_failover
    settings = get_settings().model_copy(update={"failover_mode": "failover"})

    eid = await run_pod(
        _body(),
        settings=settings,
        chat=chat,
        chat_failover=chat_failover,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert eid
    # primary 被叫 1 次（限流），failover 被叫 2 次（writer + judge）
    assert chat._call_count == 1
    assert chat_failover._call_count == 2


# ── 8. MiniMaxChatModel 構造契約（不真實打 API）───────────


def test_make_langchain_chat_construction() -> None:
    """不發 HTTP，只驗構造。"""
    settings = get_settings()
    model = make_langchain_chat(settings, engine="minimax")
    assert model.model == settings.minimax_model
    assert "minimaxi.com" in model.base_url or "minimax.io" in model.base_url
    # base_url 可能是 placeholder，值不一定匹配 .env 預設；只驗有 protocol
    assert model.base_url.startswith("http")

    api_model = make_langchain_chat(settings, engine="api_key")
    assert api_model.model == settings.api_model


def test_make_langchain_chat_unsupported_engine_raises() -> None:
    with pytest.raises(ValueError, match="不支援"):
        make_langchain_chat(engine="bogus")


# ── 9. ScriptJSON 契約：FakeChatModel 吐的字串可直接 parse ─


def test_fake_chat_response_parses_to_script_json() -> None:
    from engine.generation.prompt import parse_engine_result

    chat = _make_passing_chat()
    import asyncio

    from langchain_core.messages import HumanMessage, SystemMessage

    msg = asyncio.run(
        chat.ainvoke(
            [
                SystemMessage(content="sys"),
                HumanMessage(content="user"),
            ]
        )
    )
    result = parse_engine_result(msg.content, engine="fake", model="m", usage={})
    assert isinstance(result.script, ScriptJSON)
    assert len(result.script.script) == 8


# ── 10. resolve_format：入口類型 × 長度 tier 自動決定格式 ──


def test_resolve_format_news_always_monologue() -> None:
    from engine.pipeline.langgraph_pod.nodes import resolve_format

    assert resolve_format("news", "short") == "monologue"
    assert resolve_format("news", "long") == "monologue"


def test_resolve_format_evergreen_long_is_monologue_otherwise_dialogue() -> None:
    from engine.pipeline.langgraph_pod.nodes import resolve_format

    assert resolve_format("evergreen", "long") == "monologue"
    assert resolve_format("evergreen", "short") == "dialogue"
    assert resolve_format("evergreen", "medium") == "dialogue"


def test_resolve_format_product_always_dialogue() -> None:
    from engine.pipeline.langgraph_pod.nodes import resolve_format

    assert resolve_format("product", "short") == "dialogue"
    assert resolve_format("product", "long") == "dialogue"


# ── 11. 單人口白格式端到端：news topic_type → Nova 單人稿 ─────


async def test_pod_monologue_format_end_to_end() -> None:
    chat = FakeChatModel(
        responses=[_script_json(format="monologue")],
        judge_responses=[_judge_json(0.8)],
    )
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    body = {
        "big_topic": "AI News",
        "canonical_topic": "AI News Today",
        "angle": "定義",
        "topic_type": "news",  # news → resolve_format 一律 monologue
        "deliver_date": "2026-07-14",
        "user_ids": ["u1"],
    }
    eid = await run_pod(body, chat=chat, repo=repo, r2=r2, queue=queue, renderer=renderer)
    assert eid
    ep = repo.get_episode(eid)
    assert ep is not None
    assert ep.script_json is not None
    speakers = {line["speaker"] for line in ep.script_json["script"]}
    assert speakers == {"Nova"}


# ── 12. Grounding：注入 source_provider_factory 後 sources 進到 state ─


async def test_retrieve_sources_populates_grounded_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared.models import SourceSnippet

    class _StubProvider:
        name = "stub"

        async def fetch(self, query: str) -> list[SourceSnippet]:
            return [SourceSnippet(id="s1", title="t", url="https://x", text="真實內容")]

        async def aclose(self) -> None:
            return None

    def factory(topic_type: str, settings: object) -> _StubProvider | None:
        return _StubProvider() if topic_type == "evergreen" else None

    chat = _make_passing_chat()
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
        source_provider_factory=factory,
    )
    ep = repo.get_episode(eid)
    assert ep is not None
    assert ep.grounded is True


async def test_retrieve_sources_no_provider_keeps_ungrounded() -> None:
    """factory 回 None（如 skill 類型）→ 空 sources，episode 標記未 grounded。"""
    chat = _make_passing_chat()
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    def factory(topic_type: str, settings: object) -> None:
        return None

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
        source_provider_factory=factory,
    )
    ep = repo.get_episode(eid)
    assert ep is not None
    assert ep.grounded is False


# ── judge 韌性：code fence 與 fail-open ────────────────────


async def test_judge_fenced_json_still_parses() -> None:
    """judge 回應包 ```json fence → 剝掉照常解析，不觸發 rewrite、不殺 graph。"""
    chat = FakeChatModel(
        responses=[_script_json()],
        judge_responses=[f"```json\n{_judge_json(0.8)}\n```"],
    )
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(_body(), chat=chat, repo=repo, r2=r2, queue=queue, renderer=renderer)
    assert eid
    assert chat._call_count == 2  # writer + judge，無 rewrite


async def test_judge_garbage_fails_open() -> None:
    """judge 回垃圾（非 JSON）→ fail-open 視為通過，稿子照常出，不整集重跑。"""
    chat = FakeChatModel(
        responses=[_script_json()],
        judge_responses=["oops not json at all"],
    )
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(_body(), chat=chat, repo=repo, r2=r2, queue=queue, renderer=renderer)
    assert eid
    ep = repo.get_episode(eid)
    assert ep is not None


# ── CEFR 全鏈路：state → prompt → 落庫 ─────────────────────


async def test_cefr_flows_from_body_to_episode_row() -> None:
    """body 帶 cefr=A2 → episodes.cefr_level 落 A2（不再硬寫 B1）。"""
    chat = _make_passing_chat()
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    body = {**_body(), "cefr": "A2"}
    eid = await run_pod(body, chat=chat, repo=repo, r2=r2, queue=queue, renderer=renderer)
    ep = repo.get_episode(eid)
    assert ep is not None
    assert ep.cefr_level == "A2"


def test_build_pod_messages_cefr_and_avoid_facts() -> None:
    """分級指令與 avoid_facts 真的進到 system/user prompt；monologue 用自己的 few-shot。"""
    from engine.pipeline.langgraph_pod.nodes import _build_pod_messages

    common: dict[str, Any] = {
        "canonical_topic": "量子力學",
        "big_topic": "科技",
        "topic_type": "evergreen",
        "angle": "定義",
        "tone": "playful",
        "avoid_summary": None,
    }
    a2 = _build_pod_messages(cefr="A2", avoid_facts=("old fact",), **common)
    b2 = _build_pod_messages(cefr="B2", avoid_facts=(), **common)
    a2_system = a2[0]["content"]
    b2_system = b2[0]["content"]

    assert a2_system != b2_system  # 等級指令有差異，不是只換字數
    assert "1,500 most common" in a2_system
    assert "native-like vocabulary" in b2_system
    assert "old fact" in a2_system  # avoid_facts 進 BAN LIST
    assert "TONE: TONE" not in a2_system  # 修掉的重複前綴不回歸

    mono = _build_pod_messages(cefr="B1", avoid_facts=(), format="monologue", **common)
    assert "Nova" in mono[0]["content"]
    assert "Sarah: Mmm." not in mono[0]["content"]  # dialogue few-shot 不混進 monologue
