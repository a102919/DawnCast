"""頻道選題（planner）：backlog 不足時打一次 LLM 產生新候選，寫進 channel_topics。

跟「今天要不要生」解耦——選題可以超前執行，生成排程（daily_batch.py）只從
channel_topics 挑。地基層（shared/db/channels.py）已把 SQL 收斂好，這裡只負責
「要不要打 LLM」「打什麼 prompt」「解析結果」三件事。

設計重點：
  - count_candidates >= channel_backlog_target 就直接跳過該頻道，不打 LLM
    （存量夠了不用再想，這是成本控制的關鍵）。
  - 時事型頻道（news/product）多做一步：用既有 source provider 抓一次
    theme_prompt 的近期素材，當「有沒有東西可寫」的天然證據——不另外做證據
    檢查閘，抓不到東西時 LLM 會在 SOURCES 空的情況下自己打低分。常青頻道
    （evergreen/skill）不抓，Wikipedia 恆為真、不需要判斷「有沒有東西」。
  - MiniMax 沒有原生 tool calling / structured output，一律 prompt-instructed
    JSON：system prompt 寫死格式 → _strip_code_fence 剝 code fence →
    ChannelTopicCandidate.model_validate 解析 → 失敗重試上限 2 次 → 仍失敗
    記 warning 回空 list，不讓整批選題被單一頻道拖垮。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from engine.pipeline.langgraph_pod.chat import make_langchain_chat
from engine.pipeline.langgraph_pod.prompt import _strip_code_fence
from engine.sources.factory import make_source_provider
from shared.config import Settings, get_settings
from shared.db import channels
from shared.errors import SourceFetchError
from shared.models import ANGLES, ChannelTopicCandidate, SourceSnippet

logger = logging.getLogger(__name__)

# 解析失敗重試上限：跟 langgraph_pod/nodes.py 的 _MAX_OUTLINE_RETRIES 同精神，
# 純 parse fix、不佔生成流程的重試額度。耗盡就回空 list，不 raise——選題失敗
# 不該拖垮整批 cron（見 plan_channels docstring）。
_MAX_CANDIDATE_RETRIES = 2

# 需要外部近期素材佐證的入口類型（對齊 engine/sources/factory.py）：
# news 走 GDELT、product 走 Tavily；evergreen/skill 不查。
_SOURCED_TOPIC_TYPES = frozenset({"news", "product"})

_ANGLE_TAXONOMY = "\n".join(f"- {name}：{desc}" for name, desc in ANGLES)

_CANDIDATE_SYSTEM = f"""You are a podcast topic planner for DawnCast. Given a channel's theme, \
its recent episode history, its existing candidate backlog, and (when relevant) fresh search \
snippets about what's currently happening, propose 3-5 NEW episode topic candidates.

Each candidate has:
- canonical_topic: a specific, concrete episode topic in English (not a vague category name)
- angle: EXACTLY one of this taxonomy (output the name only, not the description):
{_ANGLE_TAXONOMY}
- rationale: 1-2 句繁體中文（台灣用詞），說明這個主題現在為什麼值得做成一集
- score: float 0.0-1.0（見下方 SCORING 錨點）
- continues_episode_slug: 若這個候選是承接／深化 RECENT_EPISODES 裡某一集，填該集的
  slug；否則為 null。只在確實是某集的延伸時才填。

# 延續性是主要目標
產生能承接或深化既有集數的下一批題目，不要重複已談過的內容：優先延伸
RECENT_EPISODES 留下的子題、挑戰或補完過去集數的角度，而不是無關的全新主題。同時
避免與 RECENT_EPISODES 或 EXISTING_CANDIDATES 已經涵蓋的「主題＋角度」組合重疊。

# SCORING（G-Eval：每個候選先照下列步驟想過一遍，再給最終 float）
evaluation_steps:
(a) 這個主題有沒有具體、可查證的內容能撐起一整集，不是空泛的類別名稱？
(b) 對照 RECENT_EPISODES 與 EXISTING_CANDIDATES，這個主題／角度組合是否明顯不同、不重疊？
(c) 若下方提供 SOURCES 區塊，裡面是否真的有可用素材支撐這個主題（時事型頻道用；沒有
    SOURCES 區塊代表這個頻道不需要外部素材，跳過這步、不影響評分）？
anchors:
- 0.9+：題目本身就值得一集，有具體可查證內容，且與已出刊集明顯不同
- 0.7：可以做，但角度需要收窄
- 0.5：勉強，與既有集重疊或內容太薄
- ≤0.3：不該做——不要輸出這種候選；寧可少於 5 個也不要為了湊數硬掰

Return ONLY JSON with this exact shape (no markdown, no code fences, no commentary):
{{"candidates": [{{"canonical_topic": str, "angle": str, "rationale": str, "score": float, \
"continues_episode_slug": str | null}}, ...]}}
Give 3-5 candidates."""


def _format_recent_episodes(recent_episodes: list[dict[str, Any]]) -> list[str]:
    """把 list_recent_channel_episodes 的列轉成 prompt 用的一行行摘要。

    extracted_facts 只取前 3 條 claim，避免整份 SourcedFact 列表把 prompt 灌爆。
    """
    lines: list[str] = []
    for ep in recent_episodes:
        facts = [f for f in (ep.get("extracted_facts") or []) if isinstance(f, dict)][:3]
        claims = "; ".join(f.get("claim", "") for f in facts) or "(無記錄)"
        lines.append(
            f"- slug={ep['slug']} title={ep['title']} angle={ep.get('angle')} facts: {claims}"
        )
    return lines


def _build_user_message(
    channel: dict[str, Any],
    recent_episodes: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]],
    snippets: list[SourceSnippet],
) -> str:
    parts = [
        f"CHANNEL THEME: {channel['theme_prompt']}",
        f"topic={channel['topic']} topic_type={channel['topic_type']} "
        f"length_tier={channel['length_tier']} cefr={channel['cefr_level']}",
        "\nRECENT_EPISODES（新→舊，最多 8 集）:",
    ]
    parts.extend(_format_recent_episodes(recent_episodes) or ["(尚未發布任何集數)"])

    parts.append("\nEXISTING_CANDIDATES（已在候選庫，避免重複）:")
    if existing_candidates:
        parts.extend(f"- {c['canonical_topic']}（{c['angle']}）" for c in existing_candidates)
    else:
        parts.append("(無)")

    if channel["topic_type"] in _SOURCED_TOPIC_TYPES:
        parts.append("\nSOURCES（近期實際發生的素材）:")
        if snippets:
            parts.extend(f"[{s.id}] {s.title}: {s.text[:300]}" for s in snippets)
        else:
            parts.append("(這次搜尋沒有抓到任何近期素材)")

    parts.append("\n產出 3-5 個候選，回傳 JSON。")
    return "\n".join(parts)


async def _fetch_snippets(channel: dict[str, Any], settings: Settings) -> list[SourceSnippet]:
    """時事型頻道抓一次 theme_prompt 的近期素材；抓取失敗降級成空 list，不擋選題。"""
    provider = make_source_provider(channel["topic_type"], settings)
    if provider is None:
        return []
    try:
        return await provider.fetch(channel["theme_prompt"])
    except SourceFetchError as exc:
        logger.warning(
            "channel_plan：頻道 %s 抓取近期素材失敗，降級成無素材: %s", channel["slug"], exc
        )
        return []
    finally:
        await provider.aclose()


async def _generate_candidates(
    chat: BaseChatModel,
    channel: dict[str, Any],
    recent_episodes: list[dict[str, Any]],
    existing_candidates: list[dict[str, Any]],
    snippets: list[SourceSnippet],
) -> list[ChannelTopicCandidate]:
    """打 LLM 產候選；解析失敗重試 _MAX_CANDIDATE_RETRIES 次，耗盡回空 list（不 raise）。"""
    base_user = _build_user_message(channel, recent_episodes, existing_candidates, snippets)

    last_exc: Exception | None = None
    for attempt in range(_MAX_CANDIDATE_RETRIES + 1):
        user = base_user
        if attempt > 0:
            user += (
                f"\n\nREVISION：上一次回應無法解析成合法結構（{last_exc}），"
                "請確實依照 JSON SCHEMA 重新輸出。"
            )
        try:
            ai_msg = await chat.ainvoke(
                [SystemMessage(content=_CANDIDATE_SYSTEM), HumanMessage(content=user)]
            )
            raw = ai_msg.content
            if not isinstance(raw, str):
                raise ValueError("選題回應不是文字")
            payload = json.loads(_strip_code_fence(raw))
            items = payload.get("candidates") if isinstance(payload, dict) else None
            if not isinstance(items, list) or not items:
                raise ValueError("選題回應缺少 candidates 陣列")
            return [ChannelTopicCandidate.model_validate(item) for item in items[:5]]
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "channel_plan：頻道 %s 選題第 %d/%d 次失敗: %s",
                channel["slug"],
                attempt + 1,
                _MAX_CANDIDATE_RETRIES + 1,
                exc,
            )
    logger.warning("channel_plan：頻道 %s 選題重試耗盡，本輪跳過: %s", channel["slug"], last_exc)
    return []


def _rationale_with_continuity(candidate: ChannelTopicCandidate) -> str:
    """continues_episode_slug 有值時附註承接對象，避免這個延續性訊號被丟棄。"""
    if candidate.continues_episode_slug:
        return f"{candidate.rationale}（承接 {candidate.continues_episode_slug}）"
    return candidate.rationale


async def _plan_one_channel(
    channel: dict[str, Any], settings: Settings, chat: BaseChatModel
) -> int:
    """單一頻道的選題流程。回傳這次實際新增的候選筆數。"""
    channel_id = channel["id"]
    backlog = await channels.count_candidates(channel_id)
    if backlog >= settings.channel_backlog_target:
        logger.info(
            "channel_plan：頻道 %s 選題庫存量 %d 已達標（目標 %d），略過 LLM",
            channel["slug"],
            backlog,
            settings.channel_backlog_target,
        )
        return 0

    recent_episodes = await channels.list_recent_channel_episodes(channel_id, 8)
    existing_candidates = await channels.list_channel_topics(channel_id, status="candidate")

    snippets: list[SourceSnippet] = []
    if channel["topic_type"] in _SOURCED_TOPIC_TYPES:
        snippets = await _fetch_snippets(channel, settings)

    candidates = await _generate_candidates(
        chat, channel, recent_episodes, existing_candidates, snippets
    )
    if not candidates:
        return 0

    payload = [
        {
            "canonical_topic": c.canonical_topic,
            "angle": c.angle,
            "rationale": _rationale_with_continuity(c),
            "score": c.score,
        }
        for c in candidates
    ]
    inserted = await channels.insert_channel_topics(channel_id, payload)
    logger.info(
        "channel_plan：頻道 %s LLM 產出 %d 筆、實際新增 %d 筆候選",
        channel["slug"],
        len(candidates),
        inserted,
    )
    return inserted


async def plan_channels(*, channel_id: str | None = None) -> int:
    """頻道選題：backlog 不足的頻道打一次 LLM 產生新候選。回傳新增候選總筆數。

    channel_id 指定時只跑該頻道（admin 手動觸發，不限 status，尊重呼叫端的明確
    選擇）；None 時跑全部 status='active' 的頻道（01:00 cron 觸發）。單一頻道
    選題失敗（LLM 或資料庫例外）只記 log 並跳過，不讓一個頻道的問題拖垮同一輪
    其他頻道的選題。
    """
    settings = get_settings()

    target_channels: list[dict[str, Any]]
    if channel_id is not None:
        channel = await channels.get_channel(channel_id)
        if channel is None:
            logger.warning("channel_plan：找不到頻道 channel_id=%s", channel_id)
            return 0
        target_channels = [channel]
    else:
        target_channels = await channels.list_channels(status="active")

    if not target_channels:
        return 0

    chat = make_langchain_chat(settings, engine=settings.generation_engine)
    total = 0
    for one_channel in target_channels:
        try:
            total += await _plan_one_channel(one_channel, settings, chat)
        except Exception:
            logger.exception("channel_plan：頻道 %s 選題流程失敗，跳過", one_channel["slug"])
    return total
