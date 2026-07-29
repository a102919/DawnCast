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
    MockQueue,
    MockR2,
    MockRenderer,
    MockRepo,
    get_mocks,
    make_mock_workdir,
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
    responses.extend(_segment_json(seg_index=i, format=format) for i in range(n_segments))
    return FakeChatModel(
        responses=responses,
        judge_responses=[_judge_json(0.8)],
    )


def _make_research_passing_chat(*, source_id: str | None) -> FakeChatModel:
    """研究圖 happy path；source_id=None 模擬 factory 無可用 provider。"""
    responses = [
        json.dumps(
            {
                "questions": [
                    {
                        "question": "量子力學的核心機制是什麼？",
                        "kind": "academic",
                        "requires_sources": True,
                    }
                ]
            }
        )
    ]
    if source_id is not None:
        responses.append(
            json.dumps(
                {
                    "verified_claims": [
                        {
                            "claim": "f1",
                            "supporting_source_ids": [source_id],
                            "contradicting_source_ids": [],
                            "confidence": 0.9,
                            "usable": True,
                        }
                    ],
                    "source_conflicts": [],
                }
            )
        )

    outline = json.loads(_outline_json())
    outline["extracted_facts"][0]["source_ids"] = [source_id] if source_id else []
    responses.append(json.dumps(outline))
    responses.extend(_segment_json(seg_index=i) for i in range(3))
    if source_id is not None:
        responses.append(
            json.dumps(
                {
                    "checks": [
                        {
                            "claim": "f1",
                            "status": "supported",
                            "source_ids": [source_id],
                        }
                    ],
                    "unsupported_ratio": 0.0,
                }
            )
        )
    return FakeChatModel(responses=responses, judge_responses=[_judge_json(0.8)])


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


PodMocks = tuple[MockRepo, MockR2, MockQueue, MockRenderer]


@pytest.fixture
def pod_mocks() -> PodMocks:
    """跑 run_pod 用的一組全新 mock：repo / r2 / queue 全重置 + 獨立 workdir renderer。"""
    repo, r2, queue = get_mocks(reset=True)
    renderer = MockRenderer(make_mock_workdir())
    return repo, r2, queue, renderer


# ── 1. happy path ────────────────────────────────────────────


async def test_pod_happy_path(pod_mocks: PodMocks) -> None:
    chat = _make_passing_chat()
    repo, r2, queue, renderer = pod_mocks

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
    # R2 物件 = N 個 segments + 1 srt。動態從 outline 算 segments 數（_make_passing_chat
    # 預設 3 但 outline 結構會擴展成更多 line），不寫死避免 fixture 變動壞。
    script_line_count = episode.script_json["script"].__len__() if episode.script_json else 0
    assert len(r2.objects) == script_line_count + 1
    assert chat._call_count == 5  # 1 outline + 3 segments + 1 judge
    # 每行 mp3 真的走「episodes/{uuid}/segments/{idx:03d}.mp3」路徑上傳，
    # 且最後一個 key 是 srt；DB row 也真的收到對應長度的 audio_keys list（向後相容
    # audio_r2_key 也帶第一個）。回歸鎖：若有人改回整集 mp3，這裡會立刻炸。
    segment_keys = [k for k in r2.objects if "/segments/" in k]
    srt_keys = [k for k in r2.objects if k.endswith(".srt")]
    assert len(segment_keys) == script_line_count
    assert len(srt_keys) == 1
    for idx, key in enumerate(sorted(segment_keys)):
        assert key.endswith(f"/segments/{idx:03d}.mp3"), key
    assert episode.audio_keys == sorted(segment_keys)
    assert episode.audio_key == sorted(segment_keys)[0]  # legacy back-compat field


# ── 2. judge 不及格 → rewrite → 及格 ──────────────────────


async def test_judge_triggers_rewrite_then_passes(pod_mocks: PodMocks) -> None:
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
    repo, r2, queue, renderer = pod_mocks

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


async def test_judge_rewrite_cap_respected(pod_mocks: PodMocks) -> None:
    """judge 永遠給爛分 → max_rewrite_iterations 次後放行，不無限循環。"""
    # max_rewrite_iterations=1：round0 + 1 次 rewrite = 2 輪
    # 2 輪 × (1 outline + 3 segments) = 8 writer + 2 judge = 10
    block = [_outline_json()] + [_segment_json(seg_index=i) for i in range(3)]
    chat = FakeChatModel(
        responses=block * 3,
        judge_responses=[_judge_json(0.3, ["bad"])] * 4,
    )
    repo, r2, queue, renderer = pod_mocks

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert eid
    assert chat._call_count == 10
    # 不會到 15（不會無限循環，第 3 輪不會發生）


async def test_judge_cap_publishes_best_draft_not_last(pod_mocks: PodMocks) -> None:
    """撞 cap 時若最後一輪比先前輪次還爛，發布歷史最佳版而非最後一版。"""
    rounds = [
        (0.5, "roundone"),  # 最佳（min=0.5）
        (0.2, "roundtwo"),  # 最後一輪、也最爛（min=0.2）→ 撞 cap
    ]
    responses = []
    for _, word in rounds:
        responses.append(_outline_json(english_word=word))
        responses.extend(_segment_json(seg_index=i, word=word) for i in range(3))
    chat = FakeChatModel(
        responses=responses,
        judge_responses=[_judge_json(score, ["bad"]) for score, _ in rounds],
    )
    repo, r2, queue, renderer = pod_mocks

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
    assert episode is not None and episode.script_json is not None
    lines_text = " ".join(line["text"] for line in episode.script_json["script"])
    assert "roundone" in lines_text
    assert "roundtwo" not in lines_text


# ── 3b. 字數低於 length_tier 下限 → 帶字數回饋重打 ───────────


async def test_short_script_triggers_length_retry_then_passes(pod_mocks: PodMocks) -> None:
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
    repo, r2, queue, renderer = pod_mocks

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


async def test_persistently_short_script_falls_back_to_longest_draft(pod_mocks: PodMocks) -> None:
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
    repo, r2, queue, renderer = pod_mocks

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


async def test_idempotent_second_call_skips_render(pod_mocks: PodMocks) -> None:
    # 第二次 run_pod 仍會從頭跑 graph，writer/judge pool 要乘 2
    # （每次 1 outline + 3 segments + 1 judge）。
    chat = FakeChatModel(
        responses=([_outline_json()] + [_segment_json(seg_index=i) for i in range(3)]) * 2,
        judge_responses=[_judge_json(0.8)] * 2,
    )
    repo, r2, queue, renderer = pod_mocks

    eid1 = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    # 第一次：N 個 segments + 1 srt（動態算 segments 數）
    ep1 = repo.get_episode(eid1)
    line_count_1 = ep1.script_json["script"].__len__() if ep1 and ep1.script_json else 0
    assert len(r2.objects) == line_count_1 + 1
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
    assert len(r2.objects) == line_count_1 + 1
    # MockRepo insert_delivery 模擬 ON CONFLICT DO NOTHING → 同 (user, ep, date)
    # 第二次不會新增。所以最終只有 2 筆（u1, u2 各一）。
    assert len(repo.deliveries) == 2


# ── 5. R2 失敗 → local fallback，R2 key 全 None ──────────


async def test_r2_failure_falls_back_to_local_keys_null(pod_mocks: PodMocks) -> None:
    """新方案下 segments 多檔沒本地 fallback：R2 失敗直接 dead_letter → DELETE row。

    舊行為：R2 失敗 → safe_local_fallback 寫整集 mp3 → update_episode_keys 帶 null key
    仍落庫 → 仍交付。新方案：segments 難以多檔 fallback，R2 失敗直接走
    storage_decision 的 dead_letter 分支清 row（跟 test_r2_failure_with_no_local_fallback
    語意一致，只是這個不走 settings.local_media_dir=None 的顯式路徑）。

    保留這個測試名稱對照舊意圖，但斷言改為「row 被刪、無交付」，避免和
    test_r2_failure_with_no_local_fallback_deletes_row 完全重複因此註解說明。

    dead_letter_node 明確把 episode_id 清成 None（row 已刪，不能讓 state 留著
    剛被刪掉的 uuid）。run_pod() 看到 storage_failed 為真時視為刻意的優雅結束，
    回傳 None 而不 raise——raise 會讓 worker vt 重投，把已經跑完的 render 整個
    重做一次，對系統性 R2 故障沒有幫助。
    """
    chat = _make_passing_chat()
    repo, _, queue, renderer = pod_mocks
    r2 = MockR2()
    r2.fail_put = True

    episode_id = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )
    assert episode_id is None
    # row 被補償清掉、沒交付（新方案：segments 沒 fallback 路徑 → dead_letter）
    assert len(repo.episodes) == 0
    assert len(repo.by_idem) == 0
    assert repo.deliveries == []


# ── 5b. R2 失敗 + 本機 fallback 也失敗 → DELETE row + raise ────


async def test_r2_failure_with_no_local_fallback_deletes_row(pod_mocks: PodMocks) -> None:
    """媒體雙重失敗不能留殭屍 row：dead_letter_node 先 DELETE，graph 本身 graceful END。

    觸發條件：local_media_dir 沒設 → safe_local_fallback 不寫檔 →
    storage_decision 分流到 dead_letter_node → DELETE row + errors 標記。
    dead_letter_node 明確把 episode_id 清成 None（row 已刪，state 不能留著
    剛被刪掉的 uuid）。run_pod() 對 storage_failed 的優雅結束回傳 None、不
    raise，worker 視為完成（read_ct 不累積），不會重投整個 render。
    """
    chat = _make_passing_chat()
    repo, r2, queue, renderer = pod_mocks
    r2.fail_put = True

    # local_media_dir=None → 沒有任何本機 fallback 機會
    settings = get_settings().model_copy(update={"local_media_dir": None})

    episode_id = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
        settings=settings,
    )
    assert episode_id is None

    # row 被補償清掉、沒交付
    assert len(repo.episodes) == 0
    assert len(repo.by_idem) == 0
    assert repo.deliveries == []


def test_storage_decision_does_not_accept_stale_fallback_file() -> None:
    config = {"configurable": {"settings": get_settings()}}
    failed_state = {
        "storage_failed": True,
        "slug": "ep-1",
    }

    assert storage_decision(failed_state, config) == "dead_letter"


# ── 6. rate-limit + 無 failover → degrade（raise RateLimitError）


async def test_rate_limit_degrade_raises_without_failover(pod_mocks: PodMocks) -> None:
    """primary 撞 429、沒給 chat_failover → run_pod 應 raise RateLimitError。"""
    chat = FakeChatModel(responses=[RateLimitError("429 mock")])
    repo, r2, queue, renderer = pod_mocks

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


async def test_rate_limit_triggers_failover_chat(pod_mocks: PodMocks) -> None:
    chat = FakeChatModel(responses=[RateLimitError("429 primary")])
    chat_failover = FakeChatModel(
        responses=[_outline_json()] + [_segment_json(seg_index=i) for i in range(3)],
        judge_responses=[_judge_json(0.8)],
    )
    repo, r2, queue, renderer = pod_mocks

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
    """ScriptJSON 契約：合法 full script JSON 可直接驗證成 ScriptJSON。"""
    result = ScriptJSON.model_validate(json.loads(_script_json()))
    assert isinstance(result, ScriptJSON)
    assert len(result.script) == 8


def test_duplicate_adjacent_zh_rejected() -> None:
    payload = json.loads(_script_json())
    payload["script"][1]["zh"] = payload["script"][0]["zh"]  # 製造相鄰 zh 完全相同
    with pytest.raises(ValueError, match="zh 完全相同"):
        ScriptJSON.model_validate(payload)


def test_simplified_char_in_zh_auto_corrected() -> None:
    """偵測到簡體字直接修正該行，不該整份 raise 逼重寫。"""
    payload = json.loads(_script_json())
    payload["script"][0]["zh"] = "两杯咖啡"  # 简体字，应自動修正成「兩杯咖啡」
    result = ScriptJSON.model_validate(payload)
    assert result.script[0].zh == "兩杯咖啡"


def test_adverb_zhi_not_treated_as_simplified() -> None:
    """「只是／只有」的「只」是正確繁體字，不該被 s2t 誤轉成量詞「隻」。

    實測發現（頻道生成第一次真實跑通）：s2t 把口語最常見的副詞用法當成
    「隻」的簡體字轉換，語意直接變錯——已補進 _TW_ACCEPTED_VARIANTS。
    """
    payload = json.loads(_script_json())
    payload["script"][0]["zh"] = "那個部分不是只發生一次，是每天發生幾百萬次。"
    result = ScriptJSON.model_validate(payload)
    assert result.script[0].zh == "那個部分不是只發生一次，是每天發生幾百萬次。"


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


def test_resolve_format_evergreen_always_dialogue() -> None:
    """evergreen 不論長短都走 dialogue（不再強制 long → monologue，見 nodes.py）。"""
    from engine.pipeline.langgraph_pod.nodes import resolve_format

    assert resolve_format("evergreen", "short") == "dialogue"
    assert resolve_format("evergreen", "medium") == "dialogue"
    assert resolve_format("evergreen", "long") == "dialogue"


def test_resolve_format_product_always_dialogue() -> None:
    from engine.pipeline.langgraph_pod.nodes import resolve_format

    assert resolve_format("product", "short") == "dialogue"
    assert resolve_format("product", "long") == "dialogue"


# ── 11. 單人口白格式端到端：news topic_type → Nova 單人稿 ─────


async def test_pod_monologue_format_end_to_end(pod_mocks: PodMocks) -> None:
    chat = _make_passing_chat(format="monologue")
    repo, r2, queue, renderer = pod_mocks

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


async def test_retrieve_sources_populates_grounded_state(
    monkeypatch: pytest.MonkeyPatch, pod_mocks: PodMocks
) -> None:
    from shared.models import SourceSnippet

    class _StubProvider:
        name = "stub"

        async def fetch(self, query: str) -> list[SourceSnippet]:
            return [SourceSnippet(id="s1", title="t", url="https://x", text="真實內容")]

        async def aclose(self) -> None:
            return None

    def factory(topic_type: str, settings: object) -> _StubProvider | None:
        return _StubProvider() if topic_type == "evergreen" else None

    chat = _make_research_passing_chat(source_id="q1:s1")
    repo, r2, queue, renderer = pod_mocks

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
    assert ep.sources == [
        {
            "id": "q1:s1",
            "title": "t",
            "url": "https://x",
            "provider": "",
            "source_type": "",
            "published_at": None,
        }
    ]


async def test_retrieve_sources_no_provider_keeps_ungrounded(pod_mocks: PodMocks) -> None:
    """factory 回 None（如 skill 類型）→ 空 sources，episode 標記未 grounded。"""
    chat = _make_research_passing_chat(source_id=None)
    repo, r2, queue, renderer = pod_mocks

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


async def test_judge_fenced_json_still_parses(pod_mocks: PodMocks) -> None:
    """judge 回應包 ```json fence → 剝掉照常解析，不觸發 rewrite、不殺 graph。"""
    chat = _make_passing_chat()
    chat.judge_responses = [f"```json\n{_judge_json(0.8)}\n```"]
    repo, r2, queue, renderer = pod_mocks

    eid = await run_pod(_body(), chat=chat, repo=repo, r2=r2, queue=queue, renderer=renderer)
    assert eid
    assert chat._call_count == 5  # 1 outline + 3 segments + 1 judge，無 rewrite


async def test_judge_garbage_fails_open(pod_mocks: PodMocks) -> None:
    """judge 回垃圾（非 JSON）→ fail-open 視為通過，稿子照常出，不整集重跑。"""
    chat = _make_passing_chat()
    chat.judge_responses = ["oops not json at all"]
    repo, r2, queue, renderer = pod_mocks

    eid = await run_pod(_body(), chat=chat, repo=repo, r2=r2, queue=queue, renderer=renderer)
    assert eid
    ep = repo.get_episode(eid)
    assert ep is not None


# ── CEFR 全鏈路：state → prompt → 落庫 ─────────────────────


async def test_cefr_flows_from_body_to_episode_row(pod_mocks: PodMocks) -> None:
    """body 帶 cefr=A2 → episodes.cefr_level 落 A2（不再硬寫 B1）。"""
    chat = _make_passing_chat()
    repo, r2, queue, renderer = pod_mocks

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


# ── 頻道機制：series_context prompt 組裝 ──────────────────────


def test_series_block_empty_when_no_context() -> None:
    """series_context 為空時整個區塊不出現（不留空標題），呼叫端沿用
    _sources_block/_verified_research_block 同款的空字串合併寫法。"""
    from engine.pipeline.langgraph_pod.nodes import _series_block

    assert _series_block(()) == ""


def test_series_block_invites_callback_without_forcing_repetition() -> None:
    """series_context 職責與 avoid_facts 相反：那邊「不要重複」，這裡「可以呼應」，
    措辭上不能變成硬性規則，也不可以鼓勵重述前幾集的內容。"""
    from engine.pipeline.langgraph_pod.nodes import _series_block

    block = _series_block(("第一集：AI 入門", "第二集：機器學習"))
    assert "第一集：AI 入門" in block
    assert "第二集：機器學習" in block
    assert "不要重述其內容" in block
    assert "若自然" in block  # 軟性建議，不是 _BAN_LIST 那種「程式會擋下來」的硬規則


def test_build_outline_messages_series_context_empty_vs_present() -> None:
    """series_context 為空與非空兩種 prompt 組裝各驗一次：空 → 不出現 SERIES
    CONTEXT 區塊；非空 → 標題真的進 outline system prompt。"""
    from engine.pipeline.langgraph_pod.nodes import _build_outline_messages

    common: dict[str, Any] = {
        "canonical_topic": "量子力學",
        "big_topic": "科技",
        "topic_type": "evergreen",
        "angle": "定義",
        "cefr": "B1",
        "tone": "playful",
        "length_tier": "medium",
        "sources": None,
        "format": "dialogue",
        "avoid_facts": (),
    }
    without_ctx = _build_outline_messages(**common, series_context=())
    with_ctx = _build_outline_messages(
        **common, series_context=("第一集：AI 入門", "第二集：機器學習")
    )

    assert "SERIES CONTEXT" not in without_ctx[0]["content"]
    assert "SERIES CONTEXT" in with_ctx[0]["content"]
    assert "第一集：AI 入門" in with_ctx[0]["content"]


def test_build_segment_messages_series_context_empty_vs_present() -> None:
    """同一件事在 segment builder 也要驗一次——outline 與 segment 兩個呼叫端
    都要吃得到 series_context（見任務要求「確認兩者都吃得到」）。"""
    from engine.pipeline.langgraph_pod.nodes import _build_segment_messages

    common: dict[str, Any] = {
        "canonical_topic": "量子力學",
        "big_topic": "科技",
        "topic_type": "evergreen",
        "angle": "定義",
        "cefr": "B1",
        "tone": "playful",
        "length_tier": "medium",
        "sources": None,
        "format": "dialogue",
        "avoid_facts": (),
        "segment_index": 0,
        "segment_count": 3,
        "segment_focus": "intro",
        "segment_vocab": [],
        "segment_word_target": 200,
        "is_chapter_boundary": False,
        "is_final_segment": False,
        "previous_tail_lines": [],
    }
    without_ctx = _build_segment_messages(**common, series_context=())
    with_ctx = _build_segment_messages(**common, series_context=("第一集：AI 入門",))

    assert "SERIES CONTEXT" not in without_ctx[0]["content"]
    assert "SERIES CONTEXT" in with_ctx[0]["content"]
    assert "第一集：AI 入門" in with_ctx[0]["content"]


# ── 13. sources 持久化：MockRepo.upsert_episode / update_episode_keys ──


async def test_mock_repo_persists_sources_at_upsert() -> None:
    """upsert_episode(sources=[...]) 落進 _EpisodeRow.sources，可由 get_episode 取出。"""
    from engine.pipeline.langgraph_pod.mock import MockRepo
    from shared.models import SourceSnippet

    repo = MockRepo()
    sources_payload = [
        SourceSnippet(id="s1", title="Quantum", url="https://q.example", text="raw body"),
        SourceSnippet(id="s2", title="Qubit", url="https://q.example/2", text="raw 2"),
    ]
    eid, _ = await repo.upsert_episode(
        idempotency_key="idem-1",
        slug="ep-quantum",
        title="Quantum",
        topic="science",
        big_topic="科技",
        angle="定義",
        topic_type="evergreen",
        sources=[s.model_dump() for s in sources_payload],
    )

    row = repo.get_episode(eid)
    assert row is not None
    # 鏡像 DB 行為：落原文（含 text），text 由 router 對外輸出時過濾掉。
    assert [s["id"] for s in row.sources] == ["s1", "s2"]
    assert row.sources[0]["text"] == "raw body"


async def test_mock_repo_upsert_default_sources_empty_list() -> None:
    """不帶 sources 參數 → row.sources 是空 list（鏡真 DB 預設 '[]'::jsonb）。"""
    from engine.pipeline.langgraph_pod.mock import MockRepo

    repo = MockRepo()
    eid, _ = await repo.upsert_episode(
        idempotency_key="idem-2",
        slug="ep-x",
        title="X",
        topic="tech",
        big_topic="科技",
        angle="定義",
        topic_type="evergreen",
    )

    row = repo.get_episode(eid)
    assert row is not None
    assert row.sources == []


async def test_mock_repo_update_episode_keys_sources_overrides_when_provided() -> None:
    """update_episode_keys(sources=[...]) → 覆寫既有 row.sources。"""
    from engine.pipeline.langgraph_pod.mock import MockRepo

    repo = MockRepo()
    eid, _ = await repo.upsert_episode(
        idempotency_key="idem-3",
        slug="ep-y",
        title="Y",
        topic="tech",
        big_topic="科技",
        angle="定義",
        topic_type="evergreen",
        sources=[{"id": "old", "title": "old", "url": "https://old", "text": "x"}],
    )

    new_sources = [
        {"id": "new1", "title": "n1", "url": "https://new1", "text": "a"},
        {"id": "new2", "title": "n2", "url": "https://new2", "text": "b"},
    ]
    await repo.update_episode_keys(
        eid,
        audio_key=None,
        srt_key=None,
        script_json={"topic": "Y"},
        cues=[],
        sources=new_sources,
    )

    row = repo.get_episode(eid)
    assert row is not None
    assert [s["id"] for s in row.sources] == ["new1", "new2"]


async def test_mock_repo_update_episode_keys_sources_none_preserves_existing() -> None:
    """update_episode_keys(sources=None) → 不動既有 row.sources（鏡真 repo 的 coalesce）。"""
    from engine.pipeline.langgraph_pod.mock import MockRepo

    repo = MockRepo()
    original = [{"id": "kept", "title": "k", "url": "https://k", "text": "ok"}]
    eid, _ = await repo.upsert_episode(
        idempotency_key="idem-4",
        slug="ep-z",
        title="Z",
        topic="tech",
        big_topic="科技",
        angle="定義",
        topic_type="evergreen",
        sources=original,
    )

    await repo.update_episode_keys(
        eid,
        audio_key=None,
        srt_key=None,
        script_json={"topic": "Z"},
        cues=[],
        # 故意不傳 sources → 既有應保留
    )

    row = repo.get_episode(eid)
    assert row is not None
    assert row.sources == original


# ── 14. Pipeline metrics：分階段耗時 + forensic run row ─────────────


async def test_pod_happy_path_records_gen_and_research_metrics(pod_mocks: PodMocks) -> None:
    """成功路徑：episode row 有完整 gen_metrics/research_metrics，forensic run 標 success。"""
    chat = _make_passing_chat()
    repo, r2, queue, renderer = pod_mocks

    eid = await run_pod(
        _body(),
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=renderer,
    )

    episode = repo.get_episode(eid)
    assert episode is not None
    assert episode.generation_started_at is not None
    assert episode.generation_finished_at is not None

    gen_metrics = episode.gen_metrics
    stage_names = [s["node"] for s in gen_metrics["stages"]]
    # 主線每個 node 都要有一筆 stage timing（順序不苛求，集合比對即可）。
    for expected in (
        "decompose_research",
        "gather_evidence",
        "cross_verify",
        "write_script",
        "quality_judge",
        "upsert_episode",
        "render_episode",
        "upload_artifacts",
        "update_episode_keys",
    ):
        assert expected in stage_names, f"缺少 stage：{expected}"
    assert gen_metrics["status"] == "success"
    assert gen_metrics["totals"]["llm_call_count"] > 0
    assert gen_metrics["totals"]["input_tokens"] > 0
    # outline + 3 segments 應該各自留一筆 llm_calls 明細，不是被合併成一筆。
    write_calls = [c for c in gen_metrics["llm_calls"] if c["node"] == "write_script"]
    assert len(write_calls) == 4  # 1 outline + 3 segments
    assert {c["call"] for c in write_calls} == {"outline", "segment"}

    research_metrics = episode.research_metrics
    assert research_metrics["judge_verdict"] == "pass"
    assert research_metrics["rewrite_iterations"] == 0
    assert "judge_scores" in research_metrics

    # forensic run：run_pod 開始就建，upsert 後補回 episode_id，成功後標 success。
    runs = [r for r in repo.pipeline_runs.values() if r.episode_id == eid]
    assert len(runs) == 1
    assert runs[0].status == "success"


async def test_pod_pre_upsert_failure_leaves_forensic_run(pod_mocks: PodMocks) -> None:
    """decompose 前置階段直接炸掉（無 chat/factory）不影響——改用「researcher 全滅仍完成」
    的情境驗證正向路徑已覆蓋；這裡改測「寫稿重試耗盡直接 raise」時 forensic run 仍留下
    status=failed 記錄，即使從未建立 episode row。
    """
    from shared.errors import GenerationError

    # outline 一律回不合法 JSON，逼 _generate_outline 重試耗盡後 raise GenerationError，
    # RetryPolicy 3 次仍失敗 → graph.ainvoke 整個炸給 run_pod 的 except 分支接住。
    chat = FakeChatModel(responses=["not json"] * 10, judge_responses=[_judge_json(0.8)])
    repo, r2, queue, renderer = pod_mocks

    with pytest.raises(GenerationError):
        await run_pod(
            _body(),
            chat=chat,
            repo=repo,
            r2=r2,
            queue=queue,
            renderer=renderer,
        )

    assert len(repo.pipeline_runs) == 1
    run = next(iter(repo.pipeline_runs.values()))
    assert run.status == "failed"
    assert run.episode_id is None  # 從沒走到 upsert_episode
    assert run.error is not None
    assert run.error["type"] == "GenerationError"
