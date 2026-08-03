"""Research 階段 nodes：decompose → gather_evidence → cross_verify → tone_selector。

抓證據、交叉驗證可用性，決定這集的語氣與格式。任何 LLM 呼叫失敗都 fail-open
退回原始題單/未驗證主張，不阻斷後續 writer 階段。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from engine.pipeline.langgraph_pod.prompt import _strip_code_fence
from shared.models import (
    EvidenceCard,
    ResearchQuestion,
    ScriptFormat,
    SourceSnippet,
    VerifiedClaim,
)

from .nodes_common import _collector, _ctx, _record_llm_usage
from .state import PodState

logger = logging.getLogger(__name__)


def resolve_format(topic_type: str, length_tier: str) -> ScriptFormat:
    """依入口類型決定格式，使用者不手動切換（PRD 重新設計 §3）。

    news    → 單人口白（快訊，Up First / Apple News+ Narrated 模式）
    其餘    → 雙主持對話（保留 SLA 對話建模與化學效應價值）

    注意：原本「evergreen 長篇 → 單人口白」會把所有長篇技術/科普講成
    Nova 獨白 7 分鐘，沒有對話節奏與角色互動；對依賴 dialogue chemistry
    的使用者體驗是直接降級，已在 2026-07-29 拿掉，一律走 dialogue。
    """
    if topic_type == "news":
        return "monologue"
    return "dialogue"


def _fallback_research_questions(state: PodState) -> list[ResearchQuestion]:
    return [
        ResearchQuestion(
            question=state.get("canonical_topic") or state["big_topic"],
            kind="general",
            requires_sources=True,
        )
    ]


_DECOMPOSE_RESEARCH_SYSTEM = """You decompose a podcast topic into focused research questions.
Return ONLY JSON with this exact shape:
{"questions": [{"question": str, "kind": "academic"|"statistics"|"claim_check"|"history"|"general",
"requires_sources": bool}]}
Give 1-6 non-overlapping questions. Prefer questions that can be checked against cited sources.
Write all questions in Traditional Chinese (Taiwan)."""


async def decompose_research_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """用既有 MiniMax chat 拆研究問題；任何失敗都退回原題單問。"""
    ctx = _ctx(config)
    collector = _collector(config)
    fallback = _fallback_research_questions(state)
    chat = ctx.get("chat")
    if (
        chat is None
        or ctx.get("source_provider_factory") is None
        or state.get("topic_type") == "skill"
    ):
        if collector is not None:
            collector.set_research_summary(questions_count=len(fallback))
        return {"research_questions": fallback}

    user = (
        f"Canonical topic: {state.get('canonical_topic') or state['big_topic']}\n"
        f"Big topic: {state['big_topic']}\n"
        f"Angle: {state.get('angle') or '定義'}"
    )
    usage: dict[str, int] | None = None
    call_start = time.monotonic()
    try:
        msg = await chat.ainvoke(
            [
                SystemMessage(content=_DECOMPOSE_RESEARCH_SYSTEM),
                HumanMessage(content=user),
            ]
        )
        usage = _record_llm_usage(
            collector, msg, node="research_decompose", call="decompose", call_start=call_start
        )
        raw = msg.content
        if not isinstance(raw, str):
            raise ValueError("研究問題拆解回應不是文字")
        payload = json.loads(_strip_code_fence(raw))
        items = payload.get("questions") if isinstance(payload, dict) else None
        if not isinstance(items, list) or not items:
            raise ValueError("研究問題拆解回應缺少 questions")
        questions = [ResearchQuestion.model_validate(item) for item in items[:6]]
    except Exception as exc:
        logger.warning(
            "decompose_research 失敗，降級成原題單問 big_topic=%s: %s",
            state.get("big_topic"),
            exc,
        )
        if collector is not None:
            collector.set_research_summary(questions_count=len(fallback))
        result: dict[str, Any] = {
            "research_questions": fallback,
            "errors": [f"decompose_research 失敗：{type(exc).__name__}"],
        }
        if usage is not None:
            result["token_usage"] = [{"node": "research_decompose", **usage}]
        return result

    if collector is not None:
        collector.set_research_summary(
            questions_count=len(questions),
            subtopics=[q.question for q in questions][:20],
        )
    return {
        "research_questions": questions,
        "token_usage": [{"node": "research_decompose", **(usage or {})}],
    }


def _evidence_source_type(snippet: SourceSnippet, provider_name: str) -> str:
    prefix, separator, _ = snippet.id.partition(":")
    return prefix if separator and prefix else provider_name


async def gather_evidence_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """逐題抓證據；單一 provider/fetch/close 失敗只記錄，不阻斷其他來源。"""
    ctx = _ctx(config)
    collector = _collector(config)
    factory = ctx.get("source_provider_factory")
    if factory is None or state.get("topic_type") == "skill":
        if collector is not None:
            collector.set_research_summary(
                source_count=0, evidence_card_count=0, grounded=False, provider_counts={}
            )
        return {"sources": [], "evidence_cards": [], "grounded": False}

    questions = (state.get("research_questions") or _fallback_research_questions(state))[:6]
    topic_type = state.get("topic_type", "evergreen")
    settings = ctx["settings"]
    sources: list[SourceSnippet] = []
    cards: list[EvidenceCard] = []
    errors: list[str] = []

    for question_index, question in enumerate(questions):
        if not question.requires_sources:
            continue
        try:
            provider = factory(topic_type, settings)
        except Exception as exc:
            logger.warning(
                "gather_evidence provider factory 失敗 question=%s: %s",
                question.question,
                exc,
            )
            errors.append(f"gather_evidence factory 失敗：{type(exc).__name__}")
            continue
        if provider is None:
            continue

        provider_name = str(getattr(provider, "name", "unknown"))
        try:
            snippets = await provider.fetch(question.question)
            for snippet_index, raw_snippet in enumerate(snippets):
                # Tavily/GDELT 的 id 是單次查詢內的序號；跨子問題時加 namespace，
                # 避免 q1:tavily:0 與 q2:tavily:0 在 citation 對映互相覆蓋。
                snippet = raw_snippet.model_copy(
                    update={"id": f"q{question_index + 1}:{raw_snippet.id}"}
                )
                sources.append(snippet)
                cards.append(
                    EvidenceCard(
                        id=f"e{question_index + 1}:{snippet_index + 1}",
                        claim=snippet.text,
                        source_ids=[snippet.id],
                        provider=raw_snippet.source or provider_name,
                        source_type=_evidence_source_type(raw_snippet, provider_name),
                        confidence=0.5,
                    )
                )
        except Exception as exc:
            logger.warning(
                "gather_evidence provider 失敗 provider=%s question=%s: %s",
                provider_name,
                question.question,
                exc,
            )
            errors.append(f"gather_evidence {provider_name} 失敗：{type(exc).__name__}")
        finally:
            try:
                await provider.aclose()
            except Exception as exc:
                logger.warning(
                    "gather_evidence provider 關閉失敗 provider=%s: %s",
                    provider_name,
                    exc,
                )
                errors.append(f"gather_evidence {provider_name} 關閉失敗：{type(exc).__name__}")

    if collector is not None:
        provider_counts: dict[str, int] = {}
        for card in cards:
            provider_counts[card.provider] = provider_counts.get(card.provider, 0) + 1
        # errors 寫進 collector 才能在 prod dashboard 看到「為什麼這集 grounded=false」
        # ── 沒這條，6 個 sub-question 全部 SourceFetchError 被 except 吞掉，表面上
        # 永遠像「沒事實佐證」，debug 一片黑（見 2026-07-29 GDELT 連線 timeout 案例）。
        summary_kwargs: dict[str, Any] = dict(
            source_count=len(sources),
            evidence_card_count=len(cards),
            grounded=bool(sources),
            provider_counts=provider_counts,
        )
        if errors:
            summary_kwargs["errors"] = errors
        collector.set_research_summary(**summary_kwargs)

    result: dict[str, Any] = {
        "sources": sources,
        "evidence_cards": cards,
        "grounded": bool(sources),
    }
    if errors:
        result["errors"] = errors
    return result


_CROSS_VERIFY_SYSTEM = """You cross-check evidence for a podcast research brief.
Compare sources, preserve contradictions, and never mark a claim usable without cited support.
Return ONLY JSON with this exact shape:
{"verified_claims": [{"claim": str, "supporting_source_ids": [str],
"contradicting_source_ids": [str], "confidence": float, "usable": bool}],
"source_conflicts": [str]}"""


def _unverified_claims(cards: list[EvidenceCard]) -> list[VerifiedClaim]:
    """交叉驗證不可用時保留候選主張，但明確標成不可採用。"""
    return [
        VerifiedClaim(
            claim=card.claim,
            supporting_source_ids=[],
            contradicting_source_ids=[],
            confidence=0.0,
            usable=False,
        )
        for card in cards
    ]


async def cross_verify_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """用 MiniMax 交叉比對證據；失敗時絕不把原始卡片假裝成已驗證。"""
    cards = list(state.get("evidence_cards") or [])
    collector = _collector(config)
    if not cards:
        if collector is not None:
            collector.set_research_summary(
                verified_claim_count=0, usable_claim_count=0, conflict_count=0
            )
        return {"verified_claims": [], "source_conflicts": []}

    ctx = _ctx(config)
    chat = ctx.get("chat")
    if chat is None:
        unverified = _unverified_claims(cards)
        if collector is not None:
            collector.set_research_summary(
                verified_claim_count=len(unverified), usable_claim_count=0, conflict_count=0
            )
        return {
            "verified_claims": unverified,
            "source_conflicts": [],
        }

    evidence_payload = [card.model_dump(mode="json") for card in cards]
    usage: dict[str, int] | None = None
    call_start = time.monotonic()
    try:
        msg = await chat.ainvoke(
            [
                SystemMessage(content=_CROSS_VERIFY_SYSTEM),
                HumanMessage(content=json.dumps(evidence_payload, ensure_ascii=False)),
            ]
        )
        usage = _record_llm_usage(
            collector, msg, node="research_cross_verify", call="cross_verify", call_start=call_start
        )
        raw = msg.content
        if not isinstance(raw, str):
            raise ValueError("交叉驗證回應不是文字")
        payload = json.loads(_strip_code_fence(raw))
        if not isinstance(payload, dict):
            raise ValueError("交叉驗證回應不是 JSON 物件")
        raw_claims = payload.get("verified_claims")
        raw_conflicts = payload.get("source_conflicts", [])
        if not isinstance(raw_claims, list) or not isinstance(raw_conflicts, list):
            raise ValueError("交叉驗證回應欄位形狀錯誤")

        available_ids = {source_id for card in cards for source_id in card.source_ids}
        verified_claims: list[VerifiedClaim] = []
        for item in raw_claims:
            claim = VerifiedClaim.model_validate(item)
            supporting = [
                source_id for source_id in claim.supporting_source_ids if source_id in available_ids
            ]
            contradicting = [
                source_id
                for source_id in claim.contradicting_source_ids
                if source_id in available_ids
            ]
            verified_claims.append(
                claim.model_copy(
                    update={
                        "supporting_source_ids": supporting,
                        "contradicting_source_ids": contradicting,
                        "usable": (claim.usable and bool(supporting) and not contradicting),
                    }
                )
            )
        source_conflicts = [item for item in raw_conflicts if isinstance(item, str)]
    except Exception as exc:
        logger.warning(
            "cross_verify 失敗，候選主張全部標成不可用 big_topic=%s: %s",
            state.get("big_topic"),
            exc,
        )
        unverified = _unverified_claims(cards)
        if collector is not None:
            collector.set_research_summary(
                verified_claim_count=len(unverified), usable_claim_count=0, conflict_count=0
            )
        result: dict[str, Any] = {
            "verified_claims": unverified,
            "source_conflicts": [],
            "errors": [f"cross_verify 失敗：{type(exc).__name__}"],
        }
        if usage is not None:
            result["token_usage"] = [{"node": "research_cross_verify", **usage}]
        return result

    if collector is not None:
        collector.set_research_summary(
            verified_claim_count=len(verified_claims),
            usable_claim_count=sum(1 for c in verified_claims if c.usable),
            conflict_count=len(source_conflicts),
        )
    return {
        "verified_claims": verified_claims,
        "source_conflicts": source_conflicts,
        "token_usage": [{"node": "research_cross_verify", **(usage or {})}],
    }


# ── Node 1: tone_selector ─────────────────────────────────


def tone_selector_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    settings = _ctx(config)["settings"]
    topic_type = state.get("topic_type", "evergreen")
    length_tier = state.get("length_tier") or "medium"
    tone = settings.tone_map.get(topic_type, "playful")
    return {
        "tone": tone,
        "length_tier": length_tier,
        "format": resolve_format(topic_type, length_tier),
    }
