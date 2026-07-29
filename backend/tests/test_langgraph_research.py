"""LangGraph pod 研究管線測試。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_research_models_enforce_contracts() -> None:
    from shared.models import (
        ClaimCheck,
        ClaimVerification,
        EvidenceCard,
        ResearchQuestion,
        VerifiedClaim,
    )

    question = ResearchQuestion(question="量子糾纏是什麼？", kind="academic")
    assert question.requires_sources is True

    card = EvidenceCard(
        id="e1",
        claim="兩粒子測量結果可能呈現關聯。",
        source_ids=["s1"],
        provider="wikipedia",
        source_type="encyclopedia",
        confidence=0.7,
    )
    assert card.limitations == []
    assert card.is_primary is False

    verified = VerifiedClaim(claim=card.claim, confidence=0.2, usable=False)
    assert verified.supporting_source_ids == []
    assert verified.contradicting_source_ids == []

    verification = ClaimVerification(
        checks=[ClaimCheck(claim=card.claim, status="uncertain")],
        unsupported_ratio=1.0,
    )
    assert verification.checks[0].source_ids == []

    with pytest.raises(ValidationError):
        EvidenceCard(
            id="e2",
            claim="沒有來源",
            source_ids=[],
            provider="stub",
            source_type="web",
            confidence=0.5,
        )


async def test_decompose_research_parse_failure_falls_back_to_original_question() -> None:
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.nodes import decompose_research_node
    from shared.config import get_settings

    chat = FakeChatModel(responses=["not-json"])
    state = {
        "big_topic": "科技",
        "canonical_topic": "量子糾纏",
        "topic_type": "evergreen",
    }
    config = {
        "configurable": {
            "chat": chat,
            "settings": get_settings(),
            "source_provider_factory": lambda topic_type, settings: None,
        }
    }

    result = await decompose_research_node(state, config)

    assert [q.model_dump() for q in result["research_questions"]] == [
        {
            "question": "量子糾纏",
            "kind": "general",
            "requires_sources": True,
        }
    ]
    assert result["errors"]
    assert result["token_usage"][0]["node"] == "research_decompose"


async def test_gather_evidence_keeps_partial_success_and_closes_every_provider() -> None:
    from engine.pipeline.langgraph_pod.nodes import gather_evidence_node
    from engine.sources.base import CombinedProvider
    from shared.config import get_settings
    from shared.errors import SourceFetchError
    from shared.models import ResearchQuestion, SourceSnippet

    class StubProvider:
        def __init__(self, name: str, *, fails: bool = False) -> None:
            self.name = name
            self.fails = fails
            self.closed = False

        async def fetch(self, query: str) -> list[SourceSnippet]:
            if self.fails:
                raise SourceFetchError(f"{self.name} failed")
            return [
                SourceSnippet(
                    id=f"{self.name}:1",
                    title=query,
                    url="https://example.com/source",
                    text=f"{query} 的證據",
                )
            ]

        async def aclose(self) -> None:
            self.closed = True

    created: list[StubProvider] = []

    # factory 現在只回傳單一 provider；用 CombinedProvider 包住一個會失敗的、
    # 一個會成功的 stub，驗證「部分來源失敗不擋其他來源」的語意不變。
    def factory(topic_type: str, settings: object) -> CombinedProvider:
        wiki_stub = StubProvider("wiki")
        broken_stub = StubProvider("broken", fails=True)
        created.extend([wiki_stub, broken_stub])
        return CombinedProvider([wiki_stub, broken_stub])

    state = {
        "big_topic": "科技",
        "topic_type": "evergreen",
        "research_questions": [
            ResearchQuestion(question="問題一", kind="academic"),
            ResearchQuestion(question="問題二", kind="history"),
        ],
    }
    config = {
        "configurable": {
            "settings": get_settings(),
            "source_provider_factory": factory,
        }
    }

    result = await gather_evidence_node(state, config)

    assert len(result["sources"]) == 2
    assert len(result["evidence_cards"]) == 2
    assert result["evidence_cards"][0].source_ids == ["q1:wiki:1"]
    # CombinedProvider 對外只有一個 name="combined"，broken_stub 的失敗被
    # CombinedProvider.fetch 內部吞掉，不再冒泡成 gather_evidence_node 的 errors。
    assert result["evidence_cards"][0].provider == "combined"
    assert result["grounded"] is True
    assert not result.get("errors")
    assert len(created) == 4
    assert all(provider.closed for provider in created)


async def test_cross_verify_preserves_source_conflicts() -> None:
    import json

    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.nodes import cross_verify_node
    from shared.config import get_settings
    from shared.models import EvidenceCard

    chat = FakeChatModel(
        responses=[
            json.dumps(
                {
                    "verified_claims": [
                        {
                            "claim": "兩份來源對成長率說法不同。",
                            "supporting_source_ids": ["q1:stats:1"],
                            "contradicting_source_ids": ["q1:news:1"],
                            "confidence": 0.4,
                            "usable": False,
                        }
                    ],
                    "source_conflicts": ["stats:1 與 news:1 的成長率互相矛盾"],
                }
            )
        ]
    )
    state = {
        "big_topic": "經濟",
        "evidence_cards": [
            EvidenceCard(
                id="e1",
                claim="兩份來源對成長率說法不同。",
                source_ids=["q1:stats:1", "q1:news:1"],
                provider="stub",
                source_type="statistics",
                confidence=0.5,
            )
        ],
    }
    config = {"configurable": {"chat": chat, "settings": get_settings()}}

    result = await cross_verify_node(state, config)

    assert result["verified_claims"][0].usable is False
    assert result["verified_claims"][0].contradicting_source_ids == ["q1:news:1"]
    assert result["source_conflicts"] == ["stats:1 與 news:1 的成長率互相矛盾"]
    assert result["token_usage"][0]["node"] == "research_cross_verify"


async def test_verify_script_claims_adds_unsupported_feedback() -> None:
    import json

    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.nodes import verify_script_claims_node
    from shared.config import get_settings
    from shared.models import ScriptJSON, SourceSnippet

    script = ScriptJSON.model_validate(
        {
            "topic": "Quantum",
            "topic_zh": "量子",
            "category": "science",
            "extracted_facts": [
                {"claim": "量子電腦已經破解所有加密。", "source_ids": ["q1:wiki:1"]}
            ],
            "target_vocab": [{"word": "quantum", "explanation": "量子"}],
            "script": [
                {
                    "speaker": "Alex" if index % 2 == 0 else "Sarah",
                    "text": f"quantum line {index}",
                    "zh": f"量子第{index}行",
                }
                for index in range(8)
            ],
            "format": "dialogue",
        }
    )
    chat = FakeChatModel(
        responses=[
            json.dumps(
                {
                    "checks": [
                        {
                            "claim": "量子電腦已經破解所有加密。",
                            "status": "unsupported",
                            "source_ids": ["q1:wiki:1"],
                        }
                    ],
                    "unsupported_ratio": 1.0,
                }
            )
        ]
    )
    state = {
        "big_topic": "科技",
        "script": script,
        "engine_used": "primary",
        "sources": [
            SourceSnippet(
                id="q1:wiki:1",
                title="量子電腦",
                url="https://example.com/quantum",
                text="現有量子電腦尚未破解所有加密。",
            )
        ],
    }
    config = {
        "configurable": {
            "chat": chat,
            "chat_failover": None,
            "settings": get_settings(),
        }
    }

    result = await verify_script_claims_node(state, config)

    assert result["claim_verification"].unsupported_ratio == 1.0
    assert result["claim_verification"].checks[0].status == "unsupported"
    assert "unsupported" in result["judge_feedback"][0]
    assert result["token_usage"][0]["node"] == "research_claim_verify"


def test_graph_routes_primary_and_failover_through_claim_verification() -> None:
    from engine.pipeline.langgraph_pod.graph import build_pod

    graph = build_pod().get_graph()
    node_names = set(graph.nodes)
    edge_pairs = {(edge.source, edge.target) for edge in graph.edges}

    assert {
        "decompose_research",
        "gather_evidence",
        "cross_verify",
        "verify_script_claims",
    } <= node_names
    assert ("__start__", "decompose_research") in edge_pairs
    assert ("decompose_research", "gather_evidence") in edge_pairs
    assert ("gather_evidence", "cross_verify") in edge_pairs
    assert ("cross_verify", "tone_selector") in edge_pairs
    assert ("write_script", "verify_script_claims") in edge_pairs
    assert ("failover_write_script", "verify_script_claims") in edge_pairs
    assert ("verify_script_claims", "quality_judge") in edge_pairs


def test_unsupported_claim_forces_existing_rewrite_until_cap() -> None:
    from engine.pipeline.langgraph_pod.nodes import judge_decision
    from shared.config import get_settings
    from shared.models import ClaimCheck, ClaimVerification

    verification = ClaimVerification(
        checks=[ClaimCheck(claim="錯誤主張", status="unsupported")],
        unsupported_ratio=1.0,
    )
    state = {
        "judge_scores": {
            "hook_strength": 1.0,
            "informativeness": 1.0,
            "pacing": 1.0,
            "chemistry": 1.0,
            "groundedness": 1.0,
        },
        "claim_verification": verification,
        "rewrite_iterations": 0,
    }
    config = {
        "configurable": {
            "quality_threshold": 0.6,
            "max_rewrite_iterations": 2,
            "settings": get_settings(),
        }
    }

    assert judge_decision(state, config) == "rewrite"
    assert judge_decision({**state, "rewrite_iterations": 2}, config) == "upsert"


def test_outline_prompt_uses_verified_claims_and_preserves_conflicts() -> None:
    from engine.pipeline.langgraph_pod.nodes import _build_outline_messages
    from shared.models import VerifiedClaim

    messages = _build_outline_messages(
        canonical_topic="量子糾纏",
        big_topic="科技",
        topic_type="evergreen",
        angle="定義",
        cefr="B1",
        tone="curious",
        length_tier="short",
        format="dialogue",
        sources=None,
        avoid_facts=(),
        verified_claims=[
            VerifiedClaim(
                claim="可採用主張",
                supporting_source_ids=["q1:wiki:1"],
                confidence=0.9,
                usable=True,
            ),
            VerifiedClaim(
                claim="不可採用主張",
                confidence=0.0,
                usable=False,
            ),
        ],
        source_conflicts=["兩個來源的統計值不同"],
    )

    system = messages[0]["content"]
    assert "VERIFIED CLAIMS" in system
    assert "可採用主張" in system
    assert "不可採用主張" not in system
    assert "兩個來源的統計值不同" in system


async def test_failover_writer_always_runs_claim_verification() -> None:
    import json

    from engine.pipeline.langgraph_pod import run_pod
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.mock import MockRenderer, get_mocks, make_mock_workdir
    from shared.config import get_settings
    from shared.errors import RateLimitError
    from shared.models import SourceSnippet
    from tests.test_langgraph_pod import _outline_json, _segment_json

    source_id = "q1:wiki:1"
    decompose = json.dumps(
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
    cross = json.dumps(
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
    primary = FakeChatModel(responses=[decompose, cross, RateLimitError("primary 429")])

    outline = json.loads(_outline_json())
    outline["extracted_facts"][0]["source_ids"] = [source_id]
    claim_check = json.dumps(
        {
            "checks": [{"claim": "f1", "status": "supported", "source_ids": [source_id]}],
            "unsupported_ratio": 0.0,
        }
    )
    failover = FakeChatModel(
        responses=[
            json.dumps(outline),
            _segment_json(seg_index=0),
            _segment_json(seg_index=1),
            _segment_json(seg_index=2),
            claim_check,
        ],
        judge_responses=[
            json.dumps(
                {
                    "hook_strength": 0.8,
                    "informativeness": 0.8,
                    "pacing": 0.8,
                    "chemistry": 0.8,
                    "groundedness": 0.8,
                    "feedback": [],
                }
            )
        ],
    )

    class Provider:
        name = "wiki"

        async def fetch(self, query: str) -> list[SourceSnippet]:
            return [
                SourceSnippet(
                    id="wiki:1",
                    title="量子力學",
                    url="https://example.com/wiki",
                    text="f1 的來源內容",
                    source="Wikipedia",
                )
            ]

        async def aclose(self) -> None:
            return None

    def factory(topic_type: str, settings: object) -> Provider:
        return Provider()

    repo, r2, queue = get_mocks(reset=True)
    settings = get_settings().model_copy(update={"failover_mode": "failover"})
    eid = await run_pod(
        {
            "big_topic": "科技",
            "canonical_topic": "量子力學",
            "angle": "定義",
            "topic_type": "evergreen",
            "deliver_date": "2026-07-14",
            "user_ids": ["u1"],
        },
        settings=settings,
        chat=primary,
        chat_failover=failover,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=MockRenderer(make_mock_workdir()),
        source_provider_factory=factory,
    )

    assert eid
    assert primary._call_count == 3
    assert failover._call_count == 6
    episode = repo.get_episode(eid)
    assert episode is not None
    assert episode.sources[0]["id"] == source_id
    assert episode.sources[0]["provider"] == "Wikipedia"


async def test_unsupported_claim_runs_existing_rewrite_loop_end_to_end() -> None:
    import json

    from engine.pipeline.langgraph_pod import run_pod
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.mock import MockRenderer, get_mocks, make_mock_workdir
    from shared.models import SourceSnippet
    from tests.test_langgraph_pod import _judge_json, _outline_json, _segment_json

    source_id = "q1:wiki:1"
    research_prefix = [
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
        ),
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
        ),
    ]
    outline = json.loads(_outline_json())
    outline["extracted_facts"][0]["source_ids"] = [source_id]
    outline_json = json.dumps(outline)
    unsupported = json.dumps(
        {
            "checks": [{"claim": "f1", "status": "unsupported", "source_ids": [source_id]}],
            "unsupported_ratio": 1.0,
        }
    )
    supported = json.dumps(
        {
            "checks": [{"claim": "f1", "status": "supported", "source_ids": [source_id]}],
            "unsupported_ratio": 0.0,
        }
    )
    writer_responses = [
        *research_prefix,
        outline_json,
        _segment_json(seg_index=0),
        _segment_json(seg_index=1),
        _segment_json(seg_index=2),
        unsupported,
        outline_json,
        _segment_json(seg_index=0),
        _segment_json(seg_index=1),
        _segment_json(seg_index=2),
        supported,
    ]
    chat = FakeChatModel(
        responses=writer_responses,
        judge_responses=[_judge_json(0.8), _judge_json(0.8)],
    )

    class Provider:
        name = "wiki"

        async def fetch(self, query: str) -> list[SourceSnippet]:
            return [
                SourceSnippet(
                    id="wiki:1",
                    title="量子力學",
                    url="https://example.com/wiki",
                    text="f1 的來源內容",
                    source="Wikipedia",
                )
            ]

        async def aclose(self) -> None:
            return None

    def factory(topic_type: str, settings: object) -> Provider:
        return Provider()

    repo, r2, queue = get_mocks(reset=True)
    eid = await run_pod(
        {
            "big_topic": "科技",
            "canonical_topic": "量子力學",
            "angle": "定義",
            "topic_type": "evergreen",
            "deliver_date": "2026-07-14",
            "user_ids": ["u1"],
        },
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=MockRenderer(make_mock_workdir()),
        source_provider_factory=factory,
    )

    assert eid
    assert chat._call_count == 14  # 兩輪 writer+verify，加兩次 quality judge
    assert repo.get_episode(eid) is not None


async def test_research_llm_and_provider_failures_degrade_to_writer() -> None:
    import json

    from engine.pipeline.langgraph_pod import run_pod
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.mock import MockRenderer, get_mocks, make_mock_workdir
    from engine.sources.base import CombinedProvider
    from shared.errors import SourceFetchError
    from shared.models import SourceSnippet
    from tests.test_langgraph_pod import _judge_json, _outline_json, _segment_json

    closed: list[str] = []

    class BrokenProvider:
        name = "broken"

        async def fetch(self, query: str) -> list[SourceSnippet]:
            raise SourceFetchError("source unavailable")

        async def aclose(self) -> None:
            closed.append("broken")
            raise RuntimeError("close unavailable")

    class GoodProvider:
        name = "good"

        async def fetch(self, query: str) -> list[SourceSnippet]:
            return [
                SourceSnippet(
                    id="good:1",
                    title="可用來源",
                    url="https://example.com/good",
                    text="可用來源內容",
                )
            ]

        async def aclose(self) -> None:
            closed.append("good")

    # BrokenProvider 排前面：驗證 CombinedProvider.aclose() 逐一關閉時，
    # 前面 provider 的 aclose() raise 不會擋到後面 provider 也被關閉
    # （見 engine/sources/base.py CombinedProvider.aclose 的 try/except）。
    def factory(topic_type: str, settings: object) -> CombinedProvider:
        return CombinedProvider([BrokenProvider(), GoodProvider()])

    outline = json.loads(_outline_json())
    outline["extracted_facts"][0]["source_ids"] = ["q1:good:1"]
    chat = FakeChatModel(
        responses=[
            "bad decompose",
            "bad cross verification",
            json.dumps(outline),
            _segment_json(seg_index=0),
            _segment_json(seg_index=1),
            _segment_json(seg_index=2),
            "bad claim verification",
        ],
        judge_responses=[_judge_json(0.8)],
    )
    repo, r2, queue = get_mocks(reset=True)

    eid = await run_pod(
        {
            "big_topic": "科技",
            "canonical_topic": "量子力學",
            "angle": "定義",
            "topic_type": "evergreen",
            "deliver_date": "2026-07-14",
            "user_ids": ["u1"],
        },
        chat=chat,
        repo=repo,
        r2=r2,
        queue=queue,
        renderer=MockRenderer(make_mock_workdir()),
        source_provider_factory=factory,
    )

    assert eid
    assert closed.count("broken") == 1
    assert closed.count("good") == 1
    assert repo.get_episode(eid) is not None
    assert chat._call_count == 8  # 研究失敗 + writer + claim verify + judge


async def test_cross_verify_parse_failure_marks_candidates_unusable() -> None:
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.nodes import cross_verify_node
    from shared.config import get_settings
    from shared.models import EvidenceCard

    state = {
        "big_topic": "科技",
        "evidence_cards": [
            EvidenceCard(
                id="e1",
                claim="候選主張",
                source_ids=["s1"],
                provider="stub",
                source_type="web",
                confidence=0.5,
            )
        ],
    }
    result = await cross_verify_node(
        state,
        {
            "configurable": {
                "chat": FakeChatModel(responses=["garbage"]),
                "settings": get_settings(),
            }
        },
    )

    assert result["verified_claims"][0].usable is False
    assert result["verified_claims"][0].confidence == 0.0
    assert result["errors"]


async def test_claim_verification_parse_failure_fails_open() -> None:
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.nodes import verify_script_claims_node
    from shared.config import get_settings
    from shared.models import ScriptJSON, SourceSnippet

    script = ScriptJSON.model_validate(
        {
            "topic": "Topic",
            "topic_zh": "主題",
            "category": "science",
            "extracted_facts": [{"claim": "事實", "source_ids": ["s1"]}],
            "target_vocab": [{"word": "quantum", "explanation": "量子"}],
            "script": [
                {
                    "speaker": "Alex" if index % 2 == 0 else "Sarah",
                    "text": f"quantum line {index}",
                    "zh": f"第{index}行",
                }
                for index in range(8)
            ],
        }
    )
    result = await verify_script_claims_node(
        {
            "big_topic": "科技",
            "script": script,
            "sources": [
                SourceSnippet(
                    id="s1",
                    title="來源",
                    url="https://example.com",
                    text="內容",
                )
            ],
        },
        {
            "configurable": {
                "chat": FakeChatModel(responses=["not json"]),
                "settings": get_settings(),
            }
        },
    )

    assert result["claim_verification"].checks == []
    assert "judge_feedback" not in result
    assert result["errors"]


async def test_skill_research_nodes_skip_external_calls() -> None:
    from engine.pipeline.langgraph_pod.chat import FakeChatModel
    from engine.pipeline.langgraph_pod.nodes import (
        cross_verify_node,
        decompose_research_node,
        gather_evidence_node,
    )
    from shared.config import get_settings

    chat = FakeChatModel(responses=[RuntimeError("should not call")])
    config = {
        "configurable": {
            "chat": chat,
            "settings": get_settings(),
            "source_provider_factory": lambda topic_type, settings: RuntimeError(
                "should not construct"
            ),
        }
    }
    state = {"big_topic": "片語", "canonical_topic": "break the ice", "topic_type": "skill"}

    questions = await decompose_research_node(state, config)
    evidence = await gather_evidence_node(
        {**state, "research_questions": questions["research_questions"]}, config
    )
    verified = await cross_verify_node(
        {**state, "evidence_cards": evidence["evidence_cards"]}, config
    )

    assert questions["research_questions"][0].kind == "general"
    assert evidence == {"sources": [], "evidence_cards": [], "grounded": False}
    assert verified == {"verified_claims": [], "source_conflicts": []}
    assert chat._call_count == 0
