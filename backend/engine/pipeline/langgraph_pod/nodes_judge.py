"""Judge 階段 nodes：逐句事實核對 → 品質評分 → rewrite 路由。

`judge_decision` 是整個 rewrite loop 的分流點；`_apply_best_draft_fallback`
確保 rewrite 次數耗盡時至少拿目前手上分數最高的草稿收尾，不會空手而歸。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from engine.pipeline.langgraph_pod.prompt import _strip_code_fence
from shared.models import (
    ClaimCheck,
    ClaimVerification,
    JudgeVerdict,
    ScriptJSON,
    ScriptLine,
    SourceSnippet,
)

from .metrics import MetricsCollector
from .nodes_common import _collector, _ctx, _record_llm_usage
from .state import PodState

logger = logging.getLogger(__name__)


_CLAIM_VERIFY_SYSTEM = """You verify factual claims in a finished podcast draft.
Check ONLY the supplied extracted_facts and their source_ids against the supplied sources.
Do not assess style, dialogue, or any uncited script line.
Return ONLY JSON with this exact shape:
{"checks": [{"claim": str, "status": "supported"|"unsupported"|"uncertain",
"source_ids": [str]}], "unsupported_ratio": float}"""


def _empty_claim_verification() -> ClaimVerification:
    return ClaimVerification(checks=[], unsupported_ratio=0.0)


async def verify_script_claims_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """核對成稿 extracted_facts；研究服務失敗時 fail-open，不阻斷既有出稿。"""
    script = state.get("script")
    sources = list(state.get("sources") or [])
    collector = _collector(config)
    if script is None or not script.extracted_facts or not sources:
        if collector is not None:
            collector.set_research_summary(
                claim_check_total=0,
                claim_check_supported=0,
                claim_check_unsupported=0,
                claim_check_unsupported_ratio=0.0,
            )
        return {"claim_verification": _empty_claim_verification()}

    ctx = _ctx(config)
    chat = ctx.get("chat_failover") if state.get("engine_used") == "failover" else ctx.get("chat")
    if chat is None:
        return {"claim_verification": _empty_claim_verification()}

    facts_payload = [fact.model_dump(mode="json") for fact in script.extracted_facts]
    sources_payload = [{"id": source.id, "text": source.text[:800]} for source in sources]
    user = json.dumps(
        {"extracted_facts": facts_payload, "sources": sources_payload},
        ensure_ascii=False,
    )
    usage: dict[str, int] | None = None
    call_start = time.monotonic()
    try:
        msg = await chat.ainvoke(
            [
                SystemMessage(content=_CLAIM_VERIFY_SYSTEM),
                HumanMessage(content=user),
            ]
        )
        usage = _record_llm_usage(
            collector, msg, node="research_claim_verify", call="verify", call_start=call_start
        )
        raw = msg.content
        if not isinstance(raw, str):
            raise ValueError("成稿主張核對回應不是文字")
        verification = ClaimVerification.model_validate_json(_strip_code_fence(raw))

        available_ids = {source.id for source in sources}
        checks_by_claim = {check.claim: check for check in verification.checks}
        checks: list[ClaimCheck] = []
        for fact in script.extracted_facts:
            allowed_ids = [source_id for source_id in fact.source_ids if source_id in available_ids]
            raw_check = checks_by_claim.get(fact.claim)
            if raw_check is None:
                checks.append(
                    ClaimCheck(
                        claim=fact.claim,
                        status="uncertain",
                        source_ids=allowed_ids,
                    )
                )
                continue

            cited_ids = [
                source_id for source_id in raw_check.source_ids if source_id in allowed_ids
            ]
            status = raw_check.status
            if status == "supported" and not cited_ids:
                status = "uncertain"
            if not allowed_ids:
                status = "unsupported"
            checks.append(
                ClaimCheck(
                    claim=fact.claim,
                    status=status,
                    source_ids=cited_ids,
                )
            )

        unsupported_count = sum(check.status != "supported" for check in checks)
        normalized = ClaimVerification(
            checks=checks,
            unsupported_ratio=unsupported_count / len(checks),
        )
    except Exception as exc:
        logger.warning(
            "verify_script_claims 失敗，安全降級略過主張核對 big_topic=%s: %s",
            state.get("big_topic"),
            exc,
        )
        result: dict[str, Any] = {
            "claim_verification": _empty_claim_verification(),
            "errors": [f"verify_script_claims 失敗：{type(exc).__name__}"],
        }
        if usage is not None:
            result["token_usage"] = [{"node": "research_claim_verify", **usage}]
        return result

    feedback = [
        f"成稿主張 {check.status}：{check.claim[:160]}"
        for check in normalized.checks
        if check.status != "supported"
    ]
    if collector is not None:
        supported = sum(1 for c in normalized.checks if c.status == "supported")
        unsupported = sum(1 for c in normalized.checks if c.status == "unsupported")
        collector.set_research_summary(
            claim_check_total=len(normalized.checks),
            claim_check_supported=supported,
            claim_check_unsupported=unsupported,
            claim_check_unsupported_ratio=normalized.unsupported_ratio,
        )
    result = {
        "claim_verification": normalized,
        "token_usage": [{"node": "research_claim_verify", **(usage or {})}],
    }
    if feedback:
        result["judge_feedback"] = feedback
    return result


# ── Node 4: quality_judge ─────────────────────────────────


_JUDGE_SYSTEM = """You are a podcast script quality judge for DawnCast. Score the script on \
5 axes (0.0-1.0). For EACH axis: first walk through the evaluation_steps below in order \
(chain-of-thought), THEN output the final float. Use these anchors: 0.0 = fails the \
described behavior entirely, 0.5 = partially present / inconsistent, 1.0 = fully and \
consistently present.

1. hook_strength — does the opening (first 1-2 lines) use one of the four hook techniques \
(curiosity gap / in medias res / counter-intuitive stat / character-led) instead of a \
generic "Today we'll talk about X" or self-introduction?
   evaluation_steps: (a) quote the opening 1-2 lines, (b) classify which hook technique (if \
any) it uses, (c) 0.0 if it's a generic intro/self-introduction, 1.0 if a clear hook technique \
lands within the first 2 lines.

2. informativeness — concrete imagery and a throughline explainer-spine analogy vs abstract, \
disconnected fact-listing.
   evaluation_steps: (a) identify whether one central analogy/image organizes the body, \
(b) count concrete sensory details vs abstract phrases, (c) 0.0 if facts are just listed with \
no organizing image, 1.0 if a clear spine analogy carries the whole episode.

3. pacing — scene-level control of tension, rhythm, and information release: does the script \
breathe and accelerate in the right places, with varied sentence length?
   evaluation_steps: (a) check for 3+ consecutive long sentences (red flag), (b) check for a \
build toward each chapter/section's mini-payoff, (c) 0.0 if monotone/uniform rhythm, 1.0 if \
rhythm clearly varies with content.

4. chemistry — ONLY meaningful for dialogue format (two hosts). Do hosts react to each other \
(questions, mild disagreement, at least one callback to something said earlier), and do BOTH \
hosts carry explanation duty (not one lecturing while the other only interjects)?
   evaluation_steps: (a) if format is monologue, skip this axis and output 1.0, (b) otherwise \
find at least one disagreement/pushback moment and one callback, (c) estimate each host's share \
of total words — if one host exceeds ~65%, cap this axis at 0.5 and say so in feedback, \
(d) 0.0 if hosts just alternate reading facts with no interaction.

5. groundedness — ONLY meaningful when SOURCES are provided below. For each entry in \
extracted_facts, check whether its source_ids point to a SOURCES entry whose text actually \
supports the claim.
   evaluation_steps: (a) if no SOURCES are provided, skip this axis and output 1.0, \
(b) otherwise for each extracted_facts claim, mark supported/unsupported by checking the cited \
source_ids's text, (c) score = supported_count / total_count.

Return ONLY this JSON (no markdown, no commentary):
{"hook_strength": float, "informativeness": float, "pacing": float, "chemistry": float, \
"groundedness": float, "feedback": [str, ...]}
At most 5 concrete, actionable feedback lines, each tied to a specific axis that scored low. \
If every axis is strong, feedback = []."""


async def quality_judge_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """LLM-as-judge：五軸 0-1 + ≤5 條 feedback（G-Eval 式 evaluation_steps + 分數錨點）。

    cheap call（小 max_tokens、structured output）。chemistry 在 monologue 格式、
    groundedness 在無 sources 時，一律覆寫成 1.0（不計入淘汰判斷，見設計文件）。

    失敗路徑：failover 過後，primary chat 沒 judge 設定；用 chat_failover 當 judge。
    """
    ctx = _ctx(config)
    collector = _collector(config)
    judge_chat = ctx.get("chat_failover") or ctx.get("chat")
    script = state.get("script")
    fmt = state.get("format", "dialogue")
    default_scores = {
        "hook_strength": 1.0,
        "informativeness": 1.0,
        "pacing": 1.0,
        "chemistry": 1.0,
        "groundedness": 1.0,
    }
    if judge_chat is None or script is None:
        return {"judge_scores": default_scores}

    sources: list[SourceSnippet] = state.get("sources") or []
    user_parts = [f"Format: {fmt}", "", "Script:", script.model_dump_json(indent=2)]
    if sources:
        user_parts.append("\nSOURCES:")
        user_parts.extend(f"[{s.id}] {s.text[:500]}" for s in sources)
    user = "\n".join(user_parts)

    # 設 judge role（FakeChatModel 會切到 judge_responses 序列）
    prev_role = getattr(judge_chat, "role", None)
    if hasattr(judge_chat, "role"):
        judge_chat.role = "judge"

    call_start = time.monotonic()
    try:
        msg = await judge_chat.ainvoke(
            [SystemMessage(content=_JUDGE_SYSTEM), HumanMessage(content=user)]
        )
        usage = _record_llm_usage(collector, msg, node="judge", call="judge", call_start=call_start)
        # judge 也可能包 ```json fence（寫稿路徑早有同樣防護，這裡補齊）。
        verdict = JudgeVerdict.model_validate_json(_strip_code_fence(msg.content))
    except Exception as exc:
        # judge 是品質加分項，不是出稿硬依賴：呼叫 / 解析失敗一律 fail-open
        # 當全數通過，讓已寫好的稿照常出——不然一個壞 judge 回應會殺掉整條
        # graph，worker vt-retry 整集重跑，白燒已花掉的寫稿 tokens。
        logger.warning(
            "quality_judge 失敗，fail-open 視為通過 big_topic=%s: %s",
            state.get("big_topic"),
            exc,
        )
        return {"judge_scores": default_scores}
    finally:
        if prev_role is not None and hasattr(judge_chat, "role"):
            judge_chat.role = prev_role

    scores = {
        "hook_strength": verdict.hook_strength,
        "informativeness": verdict.informativeness,
        "pacing": verdict.pacing,
        # monologue 沒有第二人聲可言 chemistry；無 sources 則無從查核 groundedness——
        # 兩者都不該拖垮不適用的那一軸，覆寫成 1.0 而非要求 LLM 自己記得排除。
        "chemistry": 1.0 if fmt == "monologue" else verdict.chemistry,
        "groundedness": 1.0 if not sources else verdict.groundedness,
    }
    if collector is not None:
        collector.set_research_summary(judge_scores=scores)

    result: dict[str, Any] = {
        "judge_scores": scores,
        "judge_feedback": verdict.feedback,
        "token_usage": [{"node": "judge", **usage}],
    }

    result.update(_apply_best_draft_fallback(state, ctx, scores, script))

    # [opt-p3] 整集 judge 沒過 → 額外打 per-segment judge 定位失敗段。
    # 只有 affected_segments 非空時 judge_decision 才會走 partial_rewrite;
    # 失敗或 LLM 亂答都 fallback 整輪（不設 affected_segments）。
    threshold = float(ctx.get("quality_threshold", 0.6))
    if not _judge_passed(scores, threshold) and judge_chat is not None:
        try:
            affected = await _identify_affected_segments(
                chat=judge_chat,
                previous_segment_scripts=state.get("previous_segment_scripts") or [],
                feedback=verdict.feedback,
                scores=scores,
                collector=collector,
            )
            if affected:
                result["affected_segments"] = affected
                logger.info(
                    "per-segment judge 定位失敗段 big_topic=%s: %s",
                    state.get("big_topic"),
                    affected,
                )
        except Exception as exc:
            logger.warning(
                "per-segment judge 失敗 fallback 整輪 big_topic=%s: %s",
                state.get("big_topic"),
                exc,
            )

    return result


async def _identify_affected_segments(
    *,
    chat: Any,
    previous_segment_scripts: list[list[ScriptLine]],
    feedback: list[str],
    scores: dict[str, float],
    collector: MetricsCollector | None = None,
) -> list[int]:
    """[opt-p3] 給 LLM 看依 outline segment 分組的腳本,問「哪些段是這次 judge 失敗的元兇」。

    回傳的 index 必須對齊 nodes_writer.py 的 outline segment index（消費端用
    `seg_idx not in target_segs` 逐段比對，見 write_script_node），不是腳本行數——
    之前用 `len(script.script)`（攤平的對話行數，一集常有 20-40 行）當段數代理,
    LLM 回的行索引幾乎不可能命中 outline 的段索引（通常只有 3-4 段）,導致
    partial_rewrite 誤判進場後卻沒有任何一段真的被重打。

    回傳 0-indexed segment index list；失敗或 LLM 回空就回空 list
    (caller 走整輪 rewrite 邏輯)。
    """
    n_segments = len(previous_segment_scripts)
    if n_segments <= 1:
        return []  # 單段沒 partial 意義

    feedback_text = "\n".join(f"- {f}" for f in feedback[:5]) or "(no specific feedback)"
    segments_json = json.dumps(
        [
            [line.model_dump() for line in seg_lines]
            for seg_lines in previous_segment_scripts
        ],
        ensure_ascii=False,
        indent=2,
    )
    user = (
        f"This podcast script scored these overall axes: {scores}\n"
        f"Overall feedback:\n{feedback_text}\n\n"
        f"Script, grouped into {n_segments} segments "
        f"(outer list index == segment index):\n"
        f"{segments_json}\n\n"
        f"Identify which segment indices (0-indexed, in [0, {n_segments - 1}]) "
        f"are the main cause of the low scores.\n"
        f'Return ONLY JSON: {{"affected_segments": [int, ...]}}'
    )

    call_start = time.monotonic()
    try:
        msg = await chat.ainvoke(
            [
                SystemMessage(
                    content="You are a podcast script quality judge. "
                    "Identify which segment(s) of the script caused the overall low scores. "
                    "Be conservative — only mark segments that genuinely need rewriting, "
                    "not segments that are merely 'could be slightly better'."
                ),
                HumanMessage(content=user),
            ]
        )
    except Exception as exc:
        logger.warning("per-segment judge ainvoke 失敗: %s", exc)
        return []

    _record_llm_usage(collector, msg, node="judge", call="per_segment_judge", call_start=call_start)

    try:
        payload = json.loads(_strip_code_fence(msg.content))
        if not isinstance(payload, dict):
            return []
        affected_raw = payload.get("affected_segments", [])
        if not isinstance(affected_raw, list):
            return []
        return [
            int(i) for i in affected_raw if isinstance(i, (int, float)) and 0 <= int(i) < n_segments
        ]
    except Exception as exc:
        logger.warning("per-segment judge 解析失敗: %s", exc)
        return []


def _apply_best_draft_fallback(
    state: PodState, ctx: dict[str, Any], scores: dict[str, float], script: Any
) -> dict[str, Any]:
    """best-draft 追蹤：用最弱一軸分數排名（呼應 _judge_passed 的 all-axes 門檻），
    撞 max_rewrite_iterations 時若這輪反而比歷來最佳還差，發布最佳版而非最後一版。
    """
    best_scores = state.get("best_judge_scores")
    best_min = min(best_scores.values()) if best_scores else -1.0
    current_min = min(scores.values())
    if current_min > best_min:
        return {"best_script": script, "best_judge_scores": scores}

    max_iter = int(ctx.get("max_rewrite_iterations", 1))
    iterations = state.get("rewrite_iterations", 0)
    best_script = state.get("best_script")
    if iterations >= max_iter and best_script is not None:
        return {"script": best_script, "judge_scores": best_scores}
    return {}


def _judge_passed(scores: dict[str, float], threshold: float) -> bool:
    if not scores:
        return True
    return all(v >= threshold for v in scores.values())


def judge_decision(
    state: PodState, config: RunnableConfig
) -> Literal["upsert", "rewrite", "partial_rewrite"]:
    """quality_judge 出來後的 conditional edge。

    五軸都過門檻 OR 已達 max iterations → 進 upsert；
    否則 → 回 write_script（會讀 judge_feedback 自動改寫）。
    [opt-p3] affected_segments 非空且非全部段時 → partial_rewrite（只重打失敗段）。
    """
    settings = _ctx(config)
    threshold = float(settings.get("quality_threshold", 0.6))
    max_iter = int(settings.get("max_rewrite_iterations", 1))
    scores = state.get("judge_scores") or {}
    iterations = state.get("rewrite_iterations", 0)
    verification = state.get("claim_verification")
    if isinstance(verification, ClaimVerification):
        statuses = [check.status for check in verification.checks]
    elif isinstance(verification, dict):
        statuses = [
            check.get("status")
            for check in verification.get("checks", [])
            if isinstance(check, dict)
        ]
    else:
        statuses = []
    has_unverified_claim = any(status != "supported" for status in statuses)

    affected = state.get("affected_segments") or []

    verdict: Literal["upsert", "rewrite", "partial_rewrite"]
    if iterations >= max_iter:
        verdict = "upsert"
    elif has_unverified_claim or not _judge_passed(scores, threshold):
        # [opt-p3] affected_segments 1 ≤ len < 全部段數 → partial;
        # 空（per-segment judge 沒定位到）或全段（沒 partial 意義）→ 整輪
        prev_segs = state.get("previous_segment_scripts") or []
        if affected and 1 <= len(affected) < max(len(prev_segs), 1):
            verdict = "partial_rewrite"
        else:
            verdict = "rewrite"
    else:
        verdict = "upsert"

    collector = _collector(config)
    if collector is not None:
        collector.set_research_summary(
            judge_verdict="pass" if verdict == "upsert" else "rewrite",
            rewrite_iterations=iterations,
            engine_used=state.get("engine_used"),
        )
    return verdict


# ── 紀錄 rewrite 次數（write_script 進場前 bump）───────────


async def rewrite_iteration_bump_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    return {"rewrite_iterations": state.get("rewrite_iterations", 0) + 1}


# ── Node 5: upsert_episode ────────────────────────────────


