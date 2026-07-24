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
from pathlib import Path
from typing import Any

import pytest

from engine.pipeline.langgraph_pod import run_pod
from engine.pipeline.langgraph_pod.chat import FakeChatModel, make_langchain_chat
from engine.pipeline.langgraph_pod.mock import (
    MockR2,
    MockRenderer,
    get_mocks,
    make_mock_workdir,
    safe_local_fallback,
)
from engine.pipeline.langgraph_pod.nodes import storage_decision
from shared.config import get_settings
from shared.errors import RateLimitError
from shared.models import ScriptJSON

# ── 共用 fixture ─────────────────────────────────────────────


# 分段後的寫稿管線：1 個 outline 響應 + N 個段落響應 + 1 個 judge 響應。
# Medium tier 切成 3 段（見 nodes._segment_word_targets），所以 happy-path
# 一集約需 4 個 writer 響應。每段灌到 ~400 字，3 段合計 ~1200 字，過
# medium+B1 floor 1026 但不離 word_target 1520 太遠。
_SEGMENT_WORDS = 400


def _script_json(*, format: str = "dialogue", category: str = "science") -> str:
    """完整 ScriptJSON 字串（≥8 行，總字數過 length 下限，做 fallback 範例用）。
    主要測試現在用 _outline_json + _segment_json 兩段組裝。
    """
    facts = [
        {"claim": "f1", "source_ids": []},
        {"claim": "f2", "source_ids": []},
        {"claim": "f3", "source_ids": []},
    ]
    filler = " ".join(["word"] * _SEGMENT_WORDS)
    if format == "monologue":
        script = [
            {"speaker": "Nova", "text": f"line {i} {filler} about quantum", "zh": f"第{i}行"}
            for i in range(8)
        ]
    else:
        speakers = ["Alex", "Sarah"]
        script = [
            {
                "speaker": speakers[i % 2],
                "text": f"line {i} {filler} about quantum",
                "zh": f"第{i}行",
            }
            for i in range(8)
        ]
    return json.dumps(
        {
            "topic": "Quantum",
            "topic_zh": "量子力學入門",
            "category": category,
            "extracted_facts": facts,
            "target_vocab": [{"word": "quantum", "explanation": "tiny unit"}],
            "format": format,
            "script": script,
        }
    )


def _outline_json(
    *,
    n_segments: int = 3,
    english_word: str = "quantum",
    extra_vocab: list[str] | None = None,
) -> str:
    """合法 ScriptOutline JSON：n_segments 段，每段 focus/vocab 對齊 target_vocab。

    所有段 vocab_words 都包含同一個保證字（讓分段 LLM 一定要寫進對話），
    加上可選的 extra_vocab 塞進第 1 段。"""
    target_vocab = [{"word": english_word, "explanation": "tiny unit"}]
    if extra_vocab:
        target_vocab.extend({"word": w, "explanation": w} for w in extra_vocab)
    segments = [
        {
            "focus": f"Part {i + 1} of the topic",
            "vocab_words": [english_word] + (extra_vocab if (i == 0 and extra_vocab) else []),
        }
        for i in range(n_segments)
    ]
    return json.dumps(
        {
            "topic": "Quantum",
            "topic_zh": "量子力學入門",
            "category": "science",
            "extracted_facts": [{"claim": "f1", "source_ids": []}],
            "target_vocab": target_vocab,
            "segments": segments,
        }
    )


def _segment_json(
    *,
    seg_index: int = 0,
    n_lines: int = 4,
    format: str = "dialogue",
    word: str = "quantum",
    total_words: int | None = None,
) -> str:
    """合法段落 JSON：{"script": [...]}，每行灌到指定總字數（含必含 word 過 vocab 檢查）。

    預設 4 行 × ~100 字 ≈ 400 字（medium 段目標數量級）。segment_vocab 必含
    `word`（從大綱來），這裡每行 text 都塞一次 word 確保 _vocab_words_present 通過。
    """
    if total_words is None:
        total_words = _SEGMENT_WORDS
    words_per_line = max(1, total_words // n_lines)
    if format == "monologue":
        speakers = ["Nova"] * n_lines
    else:
        speakers = ["Alex", "Sarah"]
        speakers = [speakers[i % 2] for i in range(n_lines)]
    script = [
        {
            "speaker": speakers[i],
            "text": " ".join(["word"] * words_per_line) + f" {word} part{seg_index}",
            "zh": f"段{seg_index + 1}第{i}行",
            "pause_before": False,
        }
        for i in range(n_lines)
    ]
    return json.dumps({"script": script})


def _make_passing_chat(
    *,
    n_segments: int = 3,
    format: str = "dialogue",
    extra_vocab: list[str] | None = None,
) -> FakeChatModel:
    """Happy-path: 1 個 outline + n_segments 個段落 + 1 個 judge（全過）。"""
    responses = [_outline_json(n_segments=n_segments, extra_vocab=extra_vocab)]
    responses.extend(
        _segment_json(seg_index=i, format=format) for i in range(n_segments)
    )
    return FakeChatModel(
        responses=responses,
        judge_responses=[_judge_json(0.8)],
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
    episode = repo.get_episode(eid)
    assert episode is not None
    # 分類以最終稿為準；輸入 big_topic 是「科技」，Quantum 稿仍應寫成 science。
    assert episode.topic == "science"
    assert len(repo.deliveries) == 2  # u1, u2
    assert len(r2.objects) == 2  # mp3 / srt
    assert chat._call_count == 5  # 1 outline + 3 segments + 1 judge


# ── 2. judge 不及格 → rewrite → 及格 ──────────────────────


async def test_judge_triggers_rewrite_then_passes() -> None:
    # 1 個 outline + 3 段 × 2 輪 = 7 writer + 2 judge = 9 calls
    chat = FakeChatModel(
        responses=[_outline_json()]
        + [_segment_json(seg_index=i) for i in range(3)]
        + [_outline_json()]
        + [_segment_json(seg_index=i) for i in range(3)],
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
    assert chat._call_count == 10  # 2 輪 × (1 outline + 3 segments) + 2 judge


# ── 3. judge 持續不及格 → cap 後放行 ─────────────────────


async def test_judge_rewrite_cap_respected() -> None:
    """judge 永遠給爛分 → max_rewrite_iterations 次後放行，不無限循環。"""
    # 3 輪 full generation × (1 outline + 3 segments) = 12 writer + 3 judge = 15
    block = [_outline_json()] + [_segment_json(seg_index=i) for i in range(3)]
    chat = FakeChatModel(
        responses=block * 3,
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
    assert chat._call_count == 15
    # 不會到 21（不會無限循環）


# ── 3b. 字數低於 length_tier 下限 → 帶字數回饋重打 ───────────


async def test_short_script_triggers_length_retry_then_passes() -> None:
    """第一輪 3 段各 ~30 字（合計 ~90 字遠低於 floor 1026）→ 合併後觸發 Level 2
    重打，第二輪 3 段各 ~400 字（合計 ~1200 字過 floor）→ 放行。"""
    chat = FakeChatModel(
        responses=[
            _outline_json(),
            _segment_json(seg_index=0, total_words=30),
            _segment_json(seg_index=1, total_words=30),
            _segment_json(seg_index=2, total_words=30),
            _outline_json(),
            _segment_json(seg_index=0, total_words=400),
            _segment_json(seg_index=1, total_words=400),
            _segment_json(seg_index=2, total_words=400),
        ],
        judge_responses=[_judge_json(0.8)],
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
    episode = repo.get_episode(eid)
    assert episode is not None
    assert episode.script_json is not None
    total_words = sum(len(line["text"].split()) for line in episode.script_json["script"])
    assert total_words >= 1026
    assert chat._call_count == 9  # 2 輪 × (1 outline + 3 segments) + 1 judge


async def test_persistently_short_script_falls_back_to_longest_draft() -> None:
    """3 輪 Level 2 重打都還偏短 → 字數是軟性品質目標，用歷來最長的一版出稿。"""
    block_round = [
        _outline_json(),
        _segment_json(seg_index=0, total_words=10),
        _segment_json(seg_index=1, total_words=15),
        _segment_json(seg_index=2, total_words=20),
    ]
    chat = FakeChatModel(
        responses=block_round * 3,  # 3 輪都故意拼成 ~45 字，遠低於 floor 1026
        judge_responses=[_judge_json(0.8)],
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
    episode = repo.get_episode(eid)
    assert episode is not None
    assert episode.script_json is not None
    total_words = sum(len(line["text"].split()) for line in episode.script_json["script"])
    # 3 輪都約 45 字（`_segment_json` 預設 4 行，每行填充字數 small 會帶出 base 6 字），
    # 額度用完，cap 從第一輪出稿（fallback 機制）。我們只看「順利出稿、沒 raise」，
    # 確切字數隨 segment helper 內部微調而浮動，僅驗證範圍寬鬆。
    assert 0 < total_words < 1026
    # 3 輪 × 4 calls + 1 judge = 13
    assert chat._call_count == 13


# ── 4. 冪等鍵：同 body 第二次呼叫 already_rendered=True ─


async def test_idempotent_second_call_skips_render() -> None:
    # 第二次 run_pod 仍會從頭跑 graph，writer/judge pool 要乘 2
    # （每次 1 outline + 3 segments + 1 judge）。
    chat = FakeChatModel(
        responses=([_outline_json()] + [_segment_json(seg_index=i) for i in range(3)]) * 2,
        judge_responses=[_judge_json(0.8)] * 2,
    )
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
    """媒體雙重失敗不能留殭屍 row：先 DELETE 再 graceful END。

    觸發條件：local_media_dir 沒設 → safe_local_fallback 不寫檔 →
    update_episode_keys_node 偵測 storage_failed + 無本機 mp3 → DELETE +
    return errors（不再 raise）。graph conditional edge 已分流，這裡是
    防呆路徑；測試確保 DELETE + graceful END 兩件事都發生。

    改 raise → graceful END 理由：raise 會觸發 worker pgmq 視為失敗 → vt 重投
    → render_episode 整個重做（TTS 33s+）。改 graceful END 後 worker 視為
    完成（read_ct 不累積），episode 被 compensation DELETE 不留殭屍。
    """
    chat = _make_passing_chat()
    repo, r2, queue = get_mocks(reset=True)
    r2.fail_put = True
    renderer = MockRenderer(make_mock_workdir())

    # local_media_dir=None → 沒有任何本機 fallback 機會
    settings = get_settings().model_copy(update={"local_media_dir": None})

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
        settings=settings,
    )

    # run_pod 仍回傳 episode_id（graceful END 不是例外）
    assert eid
    # row 被補償清掉、沒交付
    assert len(repo.episodes) == 0
    assert len(repo.by_idem) == 0
    assert repo.deliveries == []


def test_local_fallback_reports_write_result(tmp_path: Path) -> None:
    source = tmp_path / "episode.mp3"
    media_dir = tmp_path / "media"
    source.write_bytes(b"new-audio")
    media_dir.mkdir()

    assert safe_local_fallback(source, "ep-1", str(media_dir)) is True
    assert (media_dir / "ep-1.mp3").read_bytes() == b"new-audio"
    assert safe_local_fallback(source, "ep-1", str(tmp_path / "missing")) is False


def test_storage_decision_does_not_accept_stale_fallback_file() -> None:
    config = {"configurable": {"settings": get_settings()}}
    failed_state = {
        "storage_failed": True,
        "local_fallback_written": False,
        "slug": "ep-1",
    }

    assert storage_decision(failed_state, config) == "dead_letter"
    assert storage_decision(
        {**failed_state, "local_fallback_written": True}, config
    ) == "update_keys"


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
        responses=[_outline_json()]
        + [_segment_json(seg_index=i) for i in range(3)],
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
    assert chat_failover._call_count == 5  # 1 outline + 3 segments + 1 judge


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
    """ScriptJSON 契約：合法 full script JSON 可直接 parse_engine_result 解析。"""
    from engine.pipeline.langgraph_pod.prompt import parse_engine_result

    result = parse_engine_result(_script_json(), engine="fake", model="m", usage={})
    assert isinstance(result.script, ScriptJSON)
    assert len(result.script.script) == 8


def test_duplicate_adjacent_zh_rejected() -> None:
    payload = json.loads(_script_json())
    payload["script"][1]["zh"] = payload["script"][0]["zh"]  # 製造相鄰 zh 完全相同
    with pytest.raises(ValueError, match="zh 完全相同"):
        ScriptJSON.model_validate(payload)


def test_missing_vocab_word_rejected() -> None:
    payload = json.loads(_script_json())
    payload["target_vocab"] = [{"word": "nonexistent", "explanation": "沒出現在腳本裡"}]
    with pytest.raises(ValueError, match="沒真的出現在腳本裡"):
        ScriptJSON.model_validate(payload)


def test_vocab_word_with_inflection_accepted() -> None:
    payload = json.loads(_script_json())
    # 腳本只出現變化形 "escalated"，target_vocab 給原形 "escalate"——不該被誤判成缺字
    payload["script"][0]["text"] = "The team escalated the issue immediately."
    payload["target_vocab"] = [{"word": "escalate", "explanation": "往上呈報"}]
    script = ScriptJSON.model_validate(payload)
    assert script.target_vocab[0].word == "escalate"


def test_phrasal_verb_vocab_with_inflected_and_split_form_accepted() -> None:
    """回歸：實測踩過的真實案例。"cancel out" 這種片語動詞在對話裡常被詞形變化
    （cancels）又被受詞拆開（"cancels the noise out"），舊版整段字串比對會誤判成
    沒出現，導致寫稿契約驗證白白重試好幾次都過不了。"""
    payload = json.loads(_script_json())
    payload["script"][0]["text"] = "The headphone cancels the outside noise out completely."
    payload["target_vocab"] = [{"word": "cancel out", "explanation": "抵銷、消除"}]
    script = ScriptJSON.model_validate(payload)
    assert script.target_vocab[0].word == "cancel out"


def test_phrasal_verb_vocab_truly_missing_still_rejected() -> None:
    """片語逐字比對放寬後，仍要能抓到真的沒出現的片語（不是矯枉過正變成隨便都放行）。"""
    payload = json.loads(_script_json())
    payload["target_vocab"] = [{"word": "drown out", "explanation": "蓋過、淹沒"}]
    with pytest.raises(ValueError, match="沒真的出現在腳本裡"):
        ScriptJSON.model_validate(payload)


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
    chat = _make_passing_chat(format="monologue")
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
    chat = _make_passing_chat()
    chat.judge_responses = [f"```json\n{_judge_json(0.8)}\n```"]
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())

    eid = await run_pod(_body(), chat=chat, repo=repo, r2=r2, queue=queue, renderer=renderer)
    assert eid
    assert chat._call_count == 5  # 1 outline + 3 segments + 1 judge，無 rewrite


async def test_judge_garbage_fails_open() -> None:
    """judge 回垃圾（非 JSON）→ fail-open 視為通過，稿子照常出，不整集重跑。"""
    chat = _make_passing_chat()
    chat.judge_responses = ["oops not json at all"]
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
    """分級指令與 avoid_facts 真的進到 outline prompt；monologue 用自己的 few-shot。

    寫稿從「一次 LLM 呼叫」升級為「outline + 分段」後，_build_pod_messages 已被
    _build_outline_messages 取代；指令是否進 prompt 在這裡驗證。
    """
    from engine.pipeline.langgraph_pod.nodes import _build_outline_messages

    common: dict[str, Any] = {
        "canonical_topic": "量子力學",
        "big_topic": "科技",
        "topic_type": "evergreen",
        "angle": "定義",
        "tone": "playful",
        "length_tier": "medium",
        "sources": None,
        "format": "dialogue",
    }
    a2 = _build_outline_messages(cefr="A2", avoid_facts=("old fact",), **common)
    b2 = _build_outline_messages(cefr="B2", avoid_facts=(), **common)
    a2_system = a2[0]["content"]
    b2_system = b2[0]["content"]

    assert a2_system != b2_system  # 等級指令有差異，不是只換字數
    assert "1,500 most common" in a2_system
    assert "native-like vocabulary" in b2_system
    assert "old fact" in a2[1]["content"]  # avoid_facts 進 user prompt 第二段
    assert '"category": "tech"|"business"|"culture"|"science"' in a2_system
    assert "TONE: TONE" not in a2_system  # 修掉的重複前綴不回歸

    mono = _build_outline_messages(cefr="B1", avoid_facts=(), **{**common, "format": "monologue"})
    assert "Nova" in mono[0]["content"]
    assert "Sarah: Mmm." not in mono[0]["content"]  # dialogue few-shot 不混進 monologue


def test_sources_block_reinforces_avoid_facts_next_to_extracted_facts_rule() -> None:
    """同一批 SOURCES 常在同主題重生時被重新查到；avoid_facts 只掛在 BAN_LIST
    （開場鉤子等文風規則旁）擋不住 extracted_facts 重複引用舊事實，必須也出現在
    「extracted_facts 只能引用 SOURCES」規則旁邊才有效——這裡直接驗證那段文字。
    """
    from engine.pipeline.langgraph_pod.nodes import _sources_block
    from shared.models import SourceSnippet

    sources = [SourceSnippet(id="s1", title="t", url="https://x", text="真實內容")]

    without_avoid = _sources_block(sources, ())
    assert "old fact" not in without_avoid

    with_avoid = _sources_block(sources, ("old fact",))
    assert "old fact" in with_avoid
    # 緊鄰硬性規則，不是隨便塞在別處
    assert "extracted_facts" in with_avoid.split("old fact")[0][-200:]
