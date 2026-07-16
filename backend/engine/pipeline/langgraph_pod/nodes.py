"""LangGraph Pod 的節點函式。

每個 node 是 async callable，簽名 `(state, config) -> dict`。
state 是 PodState 的一份 copy；回傳的 dict 會被 LangGraph merge 進 state
（list 預設 replace，標 `Annotated[..., _append]` 才會 append）。
config 是 RunnableConfig；`config["configurable"]` 放 runtime context
（chat model、repo、settings 等），state 本身不背這些，避免 checkpoint 序列化失敗。

Node 邊界規則：
  * 不要 raise「控制流」例外（RateLimitError → 改設 state["rate_limited"]=True，
    conditional edge 路由）。
  * 不要 raise「預期可恢復」錯誤（StorageError → 改設 state["storage_failed"]=True，
    後續節點降級走 local fallback）。
  * 其他（GenerationError 等）才 propagate 給 RetryPolicy。
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END

from engine.generation.prompt import (
    parse_engine_result,
)
from engine.media import (
    EpisodeArtifacts,
    make_job_workdir,
    render_episode,
)
from shared.config import Settings
from shared.errors import RateLimitError, SourceFetchError
from shared.models import JudgeVerdict, ScriptFormat, ScriptJSON, SourceSnippet

from .state import PodState

logger = logging.getLogger(__name__)


# ── 長度 tier：一套參數化 scaffold，不是三份 prompt ─────────
#
# vocab 上限刻意不隨長度線性增加（long tier 額外 bonus 3-5 個，見研究：
# Oxford Bookworms 固定字彙表精神），多出的時間拿來加 chapter / 重複既有字彙。


class _TierConfig(TypedDict):
    minutes: tuple[int, int]
    chapters: int
    vocab: tuple[int, int]
    recaps: int


_LENGTH_TIERS: dict[str, _TierConfig] = {
    "short": {"minutes": (2, 3), "chapters": 1, "vocab": (3, 5), "recaps": 1},
    "medium": {"minutes": (6, 8), "chapters": 1, "vocab": (6, 8), "recaps": 1},
    "long": {"minutes": (15, 20), "chapters": 4, "vocab": (8, 12), "recaps": 2},
}

# CEFR → 語速（wpm）。取代原本寫死的「550-750 字」，用語速反推目標字數，
# 避免長度加長時語速被迫失真（研究發現：舊寫死值隱含 137-250wpm，超出自然語速）。
_CEFR_WPM: dict[str, int] = {"A2": 120, "B1": 140, "B2": 150}


def resolve_format(topic_type: str, length_tier: str) -> ScriptFormat:
    """依入口類型 × 長度 tier 自動決定格式，使用者不手動切換（PRD 重新設計 §3）。

    news        → 單人口白（快訊，Up First / Apple News+ Narrated 模式）
    evergreen 長篇 → 單人口白（深度技術解說，避免雙人虛擬人設分散注意力）
    其餘         → 雙主持對話（保留 SLA 對話建模與化學效應價值）
    """
    if topic_type == "news":
        return "monologue"
    if topic_type == "evergreen" and length_tier == "long":
        return "monologue"
    return "dialogue"


def _word_target(cefr: str, length_tier: str) -> int:
    wpm = _CEFR_WPM.get(cefr, 140)
    _, hi = _LENGTH_TIERS.get(length_tier, _LENGTH_TIERS["medium"])["minutes"]
    return wpm * hi


# ── 對應 config["configurable"] 的 runtime context ─────────


def _ctx(config: RunnableConfig) -> dict[str, Any]:
    """從 RunnableConfig 取出 runtime context，缺欄位時 raise 提醒配置錯誤。"""
    configurable = config.get("configurable") or {}
    if not configurable:
        raise RuntimeError(
            "LangGraph pod 沒收到 configurable context；"
            "請用 run_pod(body, settings) 進入點，不要直接 graph.invoke({})."
        )
    return configurable


# ── Topic / 大主題分類（沿用 production 的 _TOPIC_MAP）──────


_TOPIC_MAP: dict[str, str] = {
    "tech": "tech",
    "科技": "tech",
    "business": "business",
    "商業": "business",
    "culture": "culture",
    "文化": "culture",
    "science": "science",
    "科學": "science",
}


def _classify_topic(big_topic: str) -> str:
    return _TOPIC_MAP.get(big_topic.strip().casefold(), "tech")


def _slugify(canonical: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", canonical.casefold()).strip("_")
    base = base[:40] or "episode"
    return f"{base}_{uuid.uuid4().hex[:8]}"


# ── Vivid-writing prompt（升級版：開場鉤子 + explainer spine + grounding）─


_HOOK_TECHNIQUES = """
# OPENING HOOK（開場前 1-2 行必須用以下其中一招，禁止「Today we'll talk about/discuss...」\
或泛用自我介紹）
1. Curiosity gap：拋出問題但故意先不回答。
   例：「Last Tuesday, Sarah walked into her dream job interview. Twenty minutes \
later she walked out in tears. What happened in between?」
2. In medias res：直接空降進事件中段，不解釋。
   例：「The first thing she felt wasn't the heat — it was the silence.」
3. 反直覺數據/主張先行。
   例：「Turns out, giving people health insurance made ER visits go UP by 40%. \
That's not a typo.」
4. 人物/角色先行。
   例：「Meet the guy who accidentally got his whole country recycling — with a \
little help from organized crime.」
"""

_EXPLAINER_SPINE = """
# EXPLAINER SPINE
- 先想一個貫穿全集的中心類比或畫面，所有 facts 掛在這個類比上組織，不要逐條唸列表。
- 具體名詞勝過抽象（"the hospital cafeteria at 3am" 而非 "healthcare settings"）。
- 句子長短交錯：避免連三句都長（>12 字）。
- 每次發言盡量不超過 2-3 句，想法太長就拆成一來一往（讓對方追問、插話、簡短回應），\
不要一個人講一大段。
"""

_BAN_LIST = """
# AVOID（自動判失敗）
- "Today we'll talk about/discuss...", "Welcome back", "As we all know", \
"Let me explain", "In conclusion"
- 泛用轉場："Moving on", "Another important point", "Furthermore"
- 內文被動語態（intro 允許）
- 連兩行同字開頭
- {avoid_block}
"""

_DIALOGUE_CHEMISTRY = """
# HOST CHEMISTRY（雙主持格式）
- 主持人互相反應：提問、輕度反駁、回扣（"like you said earlier..."）。
- 至少 1-2 處立場分歧：其中一人扮演懷疑/挑毛病的角色，不要每句都用附和詞開頭。
- 至少一處 callback：呼應本集稍早提過的詞/哏，或呼應 AVOID REPETITION 區塊列出的舊集素材。
- 每人每次發言至少一個日常類比（食物、交通、家庭、天氣）。
- 預設 Alex 提問、Sarah 反駁；debate tone 時角色可換。
"""

_MONOLOGUE_VOICE = """
# SOLO NARRATOR VOICE（單人口白格式）
- 只有一個角色 Nova 對聽眾直接說話，沒有第二人聲可以互動，開場鉤子與節奏必須自己撐起來。
- 規律使用第二人稱直接對聽眾說話（"you"），並在每個轉場點加口語路標\
（"here's the thing", "let's back up", "so what does that actually mean"）。
"""

_FEW_SHOTS = """
# Few-shot exemplars（開場鉤子示範，非逐字模仿）

Example 1 (curiosity gap, topic="量子力學"):
Alex: You know that feeling when headphones go on, the world just... disappears?
Sarah: Mmm.
Alex: Imagine that, but for an electron. The electron can't take the headphones off.

Example 2 (character-led, topic="投資組合"):
Sarah: My uncle once put all his savings into one stock. One stock, Alex.
Alex: And?
Sarah: Let's say he's now a very enthusiastic fan of... index funds.

Example 3 (counter-intuitive stat, topic="remote work"):
Alex: Companies that went fully remote saw output go UP, not down. Nobody predicted that.
Sarah: Wait, really? Everyone I know assumed the opposite.
"""


_TONE_BLOCKS: dict[str, str] = {
    "curious": "TONE: curious — 提問多、答案少、留懸念。",
    "playful": "TONE: playful — 幽默、輕吐槽、生活化比喻。",
    "contemplative": "TONE: contemplative — 慢節奏、留白、安靜的洞察。",
    "debate": "TONE: debate — 兩位主持人立場分明、相互挑戰。",
}


def _structure_block(length_tier: str) -> str:
    tier = _LENGTH_TIERS.get(length_tier, _LENGTH_TIERS["medium"])
    chapters = tier["chapters"]
    vocab_lo, vocab_hi = tier["vocab"]
    recaps = tier["recaps"]
    if chapters <= 1:
        chapter_line = "- Body：圍繞指定角度單線推進，不要分 chapter。"
    else:
        chapter_line = (
            f"- Body 拆成 {chapters} 個 chapter，每個對應 ANGLES taxonomy 中不同的一個角度"
            "（例如：定義→歷史→應用場景→常見誤解），各自有 hook→development→payoff 的小結構；"
            "chapter 之間插入明確的 reset/transition 句（簡短回顧前段 + 一句話帶到下一段），"
            "該行的 pause_before 設 true。"
        )
    recap_line = (
        "- 全集只需一次 recap（結尾）。"
        if recaps <= 1
        else "- 除了結尾 recap，額外在整集中段（約第 2 個 chapter 結束處）插入一次中途 recap，"
        "避免長篇聽眾在四分之一處失去專注。"
    )
    return (
        f"# STRUCTURE\n"
        f"- 目標字彙 {vocab_lo}-{vocab_hi} 個，隨內容自然帶出（不要開頭一次列完）；"
        "長篇可額外加 3-5 個高價值加碼字，但既有字彙數不因長度增加而膨脹"
        "（多出的時間用來對同一組字彙做不同語境的重複）。\n"
        f"{chapter_line}\n{recap_line}"
    )


def _sources_block(sources: list[SourceSnippet]) -> str:
    """把抓到的真實資料編號注入 prompt；空 sources 時退化成純 LLM 生成（沿用現況行為）。"""
    if not sources:
        return ""
    lines = ["\n# SOURCES（真實資料，extracted_facts 只能引用這裡列出的內容）"]
    for s in sources:
        date = f"，{s.published_at}" if s.published_at else ""
        lines.append(f"[{s.id}] {s.title}{date}\n{s.text[:800]}")
    lines.append(
        "\nextracted_facts 裡每一條宣稱都要在 source_ids 填對應的 [id]，且內容必須來自上面"
        "的 SOURCES；沒有對應來源支持的內容不要放進 extracted_facts。"
        "對話裡的個人風格、比喻、玩笑、banter 不受此限——只有事實宣稱被查核，不是整份稿子。"
    )
    return "\n".join(lines)


def _build_pod_messages(
    *,
    canonical_topic: str,
    big_topic: str,
    topic_type: str,
    angle: str,
    cefr: str,
    tone: str,
    length_tier: str = "medium",
    format: ScriptFormat = "dialogue",
    sources: list[SourceSnippet] | None = None,
    avoid_summary: str | None,
    avoid_facts: tuple[str, ...],
    feedback: list[str] | None = None,
) -> list[dict[str, str]]:
    """組 system + user messages；feedback 非空時追加 rewrite 指令。"""
    angles = {
        "定義": "這是什麼、核心概念入門",
        "人物故事": "關鍵人物 / 真實案例切入",
        "常見誤解": "破除迷思、澄清誤會",
        "應用場景": "日常生活 / 職場怎麼用上",
        "歷史": "起源與演變",
        "對比": "與相似概念的差異",
    }
    angle_desc = angles.get(angle, "")
    tones_block = _TONE_BLOCKS.get(tone, _TONE_BLOCKS["playful"])
    lo, hi = _LENGTH_TIERS.get(length_tier, _LENGTH_TIERS["medium"])["minutes"]
    word_target = _word_target(cefr, length_tier)

    avoid_lines = []
    if avoid_facts:
        avoid_lines.append("Do NOT repeat these facts already covered:")
        avoid_lines.extend(f"- {f}" for f in avoid_facts)
    if avoid_summary:
        avoid_lines.append(f"Previous summary: {avoid_summary}")
    avoid_block = "\n".join(avoid_lines) if avoid_lines else "(none)"

    if format == "monologue":
        cast_line = "Write a solo narration by ONE host: Nova, speaking directly to the listener."
        voice_block = _MONOLOGUE_VOICE
        schema_speaker = '"Nova"'
    else:
        cast_line = "Write a natural, friendly conversation between TWO hosts: Alex and Sarah."
        voice_block = _DIALOGUE_CHEMISTRY
        schema_speaker = '"Alex"|"Sarah"'

    system = (
        f"You are the head writer for DawnCast, a daily English-learning podcast. {cast_line}\n\n"
        f"# AUDIENCE & LEVEL\n- CEFR {cefr}. Common everyday vocabulary, simple sentences.\n\n"
        f"# LENGTH\n- {lo}-{hi} minutes spoken, about {word_target} English words total.\n\n"
        f"# ANGLE\n- {angle}（{angle_desc}）— 全集都圍繞這個角度。\n\n"
        "# BILINGUAL\n- Every line MUST have `zh` in natural Taiwan Mandarin (台灣正體中文), "
        "translate the meaning naturally, NOT word-for-word.\n\n"
        "# OUTPUT\n- Output ONLY the JSON object. No markdown, no code fences, no commentary.\n\n"
        f"TONE: {tones_block}\n"
        f"{_HOOK_TECHNIQUES}"
        f"{_EXPLAINER_SPINE}"
        f"{voice_block}"
        f"{_BAN_LIST.format(avoid_block=avoid_block)}"
        f"{_structure_block(length_tier)}\n\n"
        f"{_FEW_SHOTS}"
        f"{_sources_block(sources or [])}\n\n"
        "JSON SCHEMA (must match exactly):\n"
        '{"topic": str, '
        '"extracted_facts": [{"claim": str, "source_ids": [str]}], '
        '"target_vocab": [{"word": str, "explanation": str}], '
        f'"format": "{format}", '
        '"script": [{"speaker": ' + schema_speaker + ', "text": str, "zh": str, '
        '"pause_before": bool}]}'
    )

    user_parts = [
        "Write today's episode.",
        f"- Canonical topic: {canonical_topic}",
        f"- Big topic: {big_topic}",
        f"- Topic type: {topic_type}",
        f"- Angle: {angle}（{angle_desc}）",
    ]
    if feedback:
        user_parts.append("\nREVISION INSTRUCTIONS from quality judge:")
        user_parts.extend(f"- {line}" for line in feedback)
        user_parts.append(
            "\nRewrite the script incorporating the above. Keep the same topic/angle."
        )
    user_parts.append("\nReturn the JSON object now.")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def _to_lc_messages(msgs: list[dict[str, str]]) -> list[Any]:
    out: list[Any] = []
    for m in msgs:
        if m["role"] == "system":
            out.append(SystemMessage(content=m["content"]))
        elif m["role"] == "user":
            out.append(HumanMessage(content=m["content"]))
    return out


def _usage_from_ai_msg(ai_msg: Any) -> dict[str, object]:
    """從 chat.py 塞進 AIMessage.usage_metadata 的量抽出來；FakeChatModel 沒填時回 0。"""
    meta = getattr(ai_msg, "usage_metadata", None) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
    }


# ── Node 0: retrieve_sources ──────────────────────────────


async def retrieve_sources_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """依 topic_type 抓真實資料當 grounding 素材。

    factory 未注入（mock/test 模式）或該 topic_type 沒有對應 provider（如 skill）
    → 回空 sources，寫稿照舊走純 LLM 生成（等同現況行為，不阻斷主流程）。
    抓取失敗（timeout / API 掛掉）同樣降級成空 sources，不 raise 給 RetryPolicy——
    真實資料是加分項，不是生成的硬依賴。
    """
    ctx = _ctx(config)
    factory = ctx.get("source_provider_factory")
    if factory is None:
        return {"sources": [], "grounded": False}

    settings = ctx["settings"]
    topic_type = state.get("topic_type", "evergreen")
    provider = factory(topic_type, settings)
    if provider is None:
        return {"sources": [], "grounded": False}

    query = state.get("canonical_topic") or state["big_topic"]
    try:
        sources = await provider.fetch(query)
    except SourceFetchError as exc:
        logger.warning(
            "retrieve_sources 失敗，降級成無 grounding topic_type=%s: %s", topic_type, exc
        )
        sources = []
    finally:
        await provider.aclose()

    return {"sources": sources, "grounded": bool(sources)}


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


# ── Node 2: write_script ─────────────────────────────────


async def write_script_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """打 LLM 寫稿。RateLimitError → 設 rate_limited=True，不 raise。

    GenerationError 由 RetryPolicy（max_attempts=3）重試；語意層重生風暴由 policy 守。
    """
    chat = _ctx(config)["chat"]
    settings = _ctx(config)["settings"]

    feedback = state.get("judge_feedback") or None
    msgs = _build_pod_messages(
        canonical_topic=state["canonical_topic"],
        big_topic=state["big_topic"],
        topic_type=state["topic_type"],
        angle=state["angle"],
        cefr=settings.cefr_level if hasattr(settings, "cefr_level") else "B1",
        tone=state.get("tone", "playful"),
        length_tier=state.get("length_tier") or "medium",
        format=state.get("format", "dialogue"),
        sources=state.get("sources"),
        avoid_summary=None,
        avoid_facts=(),
        feedback=feedback,
    )

    try:
        ai_msg = await chat.ainvoke(_to_lc_messages(msgs))
    except RateLimitError:
        logger.warning("write_script 撞限流 big_topic=%s", state["big_topic"])
        return {"rate_limited": True, "engine_used": "primary"}

    usage = _usage_from_ai_msg(ai_msg)
    try:
        result = parse_engine_result(
            ai_msg.content,
            engine=getattr(chat, "_llm_type", "chat"),
            model=getattr(chat, "model", "unknown"),
            usage=usage,
        )
    except Exception:
        # 語意層失敗：GenerationError → 給 RetryPolicy；其他解析錯誤也照樣 raise
        raise

    return {
        "script": result.script,
        "engine_used": result.engine,
        "rate_limited": False,
        "token_usage": [{"node": "write_script", **usage}],
    }


# ── Node 3: failover_write_script ────────────────────────


async def failover_write_script_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """failover_mode=failover 時由 conditional edge 路由過來。

    用 config 裡預建好的 chat_failover（api_key 引擎）重打一次。
    """
    chat = _ctx(config).get("chat_failover")
    if chat is None:
        return {
            "rate_limited": False,
            "errors": ["failover requested but no chat_failover configured"],
        }

    settings = _ctx(config)["settings"]
    msgs = _build_pod_messages(
        canonical_topic=state["canonical_topic"],
        big_topic=state["big_topic"],
        topic_type=state["topic_type"],
        angle=state["angle"],
        cefr=settings.cefr_level if hasattr(settings, "cefr_level") else "B1",
        tone=state.get("tone", "playful"),
        length_tier=state.get("length_tier") or "medium",
        format=state.get("format", "dialogue"),
        sources=state.get("sources"),
        avoid_summary=None,
        avoid_facts=(),
        feedback=state.get("judge_feedback") or None,
    )

    try:
        ai_msg = await chat.ainvoke(_to_lc_messages(msgs))
    except RateLimitError:
        return {
            "rate_limited": True,
            "engine_used": "failover",
            "errors": ["failover engine also rate-limited"],
        }

    usage = _usage_from_ai_msg(ai_msg)
    result = parse_engine_result(
        ai_msg.content,
        engine="failover",
        model=getattr(chat, "model", "unknown"),
        usage=usage,
    )
    return {
        "script": result.script,
        "engine_used": result.engine,
        "rate_limited": False,
        "token_usage": [{"node": "write_script_failover", **usage}],
    }


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
(questions, mild disagreement, at least one callback to something said earlier)?
   evaluation_steps: (a) if format is monologue, skip this axis and output 1.0, (b) otherwise \
find at least one disagreement/pushback moment and one callback, (c) 0.0 if hosts just \
alternate reading facts with no interaction.

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

    try:
        msg = await judge_chat.ainvoke(
            [SystemMessage(content=_JUDGE_SYSTEM), HumanMessage(content=user)]
        )
        usage = _usage_from_ai_msg(msg)
        verdict = JudgeVerdict.model_validate_json(msg.content)
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
    return {
        "judge_scores": scores,
        "judge_feedback": verdict.feedback,
        "token_usage": [{"node": "judge", **usage}],
    }


def _judge_passed(scores: dict[str, float], threshold: float) -> bool:
    if not scores:
        return True
    return all(v >= threshold for v in scores.values())


def judge_decision(state: PodState, config: RunnableConfig) -> Literal["upsert", "rewrite"]:
    """quality_judge 出來後的 conditional edge。

    五軸都過門檻 OR 已達 max iterations → 進 upsert；
    否則 → 回 write_script（會讀 judge_feedback 自動改寫）。
    """
    settings = _ctx(config)
    threshold = float(settings.get("quality_threshold", 0.6))
    max_iter = int(settings.get("max_rewrite_iterations", 2))
    scores = state.get("judge_scores") or {}
    iterations = state.get("rewrite_iterations", 0)

    if _judge_passed(scores, threshold) or iterations >= max_iter:
        return "upsert"
    return "rewrite"


# ── 紀錄 rewrite 次數（write_script 進場前 bump）───────────


async def rewrite_iteration_bump_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    return {"rewrite_iterations": state.get("rewrite_iterations", 0) + 1}


# ── Node 5: upsert_episode ────────────────────────────────


async def upsert_episode_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]

    script: ScriptJSON = state["script"]
    cluster_id = state.get("cluster_id")
    deliver_date = state["deliver_date"]
    big_topic = state["big_topic"]
    angle = state["angle"]
    canonical = state["canonical_topic"]
    length_tier = state.get("length_tier") or "medium"
    topic_type = state.get("topic_type") or "evergreen"

    # 冪等鍵同帶 length_tier 與 topic_type：同日同 big_topic 但不同入口或長度
    # 的請求不能共用同一列（否則後送的會覆蓋先前已渲染的集數）。
    # format 是 derived（=resolve_format(topic_type, length_tier)），不重複併入。
    idem_key = f"{cluster_id or f'{deliver_date}:{big_topic}:{angle}'}:{length_tier}:{topic_type}"
    slug = _slugify(canonical)
    script_format = state.get("format", "dialogue")
    grounded = bool(state.get("grounded"))

    usage_log = state.get("token_usage") or []
    total_in = sum(int(u.get("input_tokens", 0)) for u in usage_log)
    total_out = sum(int(u.get("output_tokens", 0)) for u in usage_log)

    if hasattr(repo, "upsert_episode"):  # MockRepo
        episode_id, already_rendered = await repo.upsert_episode(
            idempotency_key=idem_key,
            slug=slug,
            title=script.topic,
            topic=_classify_topic(big_topic),
            big_topic=big_topic,
            angle=angle,
            topic_type=state["topic_type"],
            cefr_level="B1",
            title_zh=big_topic,
            cluster_id=cluster_id,
            length_tier=length_tier,
            format=script_format,
            grounded=grounded,
            input_tokens=total_in,
            output_tokens=total_out,
        )
    else:
        from shared.db import repo as real_repo  # noqa: PLC0415

        episode_id, already_rendered = await real_repo.upsert_episode(
            idempotency_key=idem_key,
            slug=slug,
            title=script.topic,
            topic=_classify_topic(big_topic),
            big_topic=big_topic,
            angle=angle,
            topic_type=state["topic_type"],
            cefr_level="B1",
            title_zh=big_topic,
            cluster_id=cluster_id,
            length_tier=length_tier,
            format=script_format,
            grounded=grounded,
            input_tokens=total_in,
            output_tokens=total_out,
        )

    if usage_log:
        logger.info(
            "generate token 用量 episode_id=%s big_topic=%s input=%d output=%d total=%d calls=%d",
            episode_id,
            big_topic,
            total_in,
            total_out,
            total_in + total_out,
            len(usage_log),
        )

    return {"episode_id": episode_id, "slug": slug, "already_rendered": already_rendered}


def render_branch_decision(state: PodState) -> Literal["render", "deliveries"]:
    return "deliveries" if state.get("already_rendered") else "render"


# ── Node 6: render_episode ────────────────────────────────


async def render_episode_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    renderer = ctx.get("renderer")  # None → 用 production render_episode

    script: ScriptJSON = state["script"]

    if renderer is not None:
        # mock 路徑
        from .mock import MockRenderer, make_mock_workdir  # noqa: PLC0415

        workdir = make_mock_workdir()
        if not isinstance(renderer, MockRenderer):
            raise TypeError("renderer 不是 MockRenderer")
        script_payload = script.model_dump()
        mp3, mp4, srt, cues = renderer.render(script_payload)
        return {
            "artifacts": EpisodeArtifacts(
                mp3_path=mp3,
                mp4_path=mp4,
                srt=srt,
                vtt="",  # mock 不產
                cues=[__import__("shared.models", fromlist=["Cue"]).Cue(**c) for c in cues],
            ),
        }

    # production 路徑
    # workdir 不能用 auto-cleanup 的 TemporaryDirectory：mp3/mp4 檔要活到
    # upload_artifacts_node（下一個 node）讀完才能刪，見 upload_artifacts_node 的 finally。
    _settings: Settings = ctx["settings"]  # noqa: F841  預留觀測 / 後續設定接入
    workdir = make_job_workdir()
    artifacts = await render_episode(script, workdir)
    return {"artifacts": artifacts}


# ── Node 7: upload_artifacts ──────────────────────────────


async def upload_artifacts_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    r2 = ctx.get("r2")
    settings: Settings = ctx["settings"]

    episode_id = state["episode_id"]
    slug = state["slug"]
    art: EpisodeArtifacts = state["artifacts"]

    prefix = f"episodes/{episode_id}"
    audio_key = f"{prefix}/episode.mp3"
    mp4_key = f"{prefix}/episode.mp4"
    srt_key = f"{prefix}/episode.srt"

    is_production = r2 is None  # mock 路徑會注入 MockR2；production 沒有才走真 R2

    storage_failed = False
    try:
        if r2 is not None:
            r2.put_object(audio_key, art.mp3_path.read_bytes(), "audio/mpeg")
            r2.put_object(mp4_key, art.mp4_path.read_bytes(), "video/mp4")
            r2.put_object(srt_key, art.srt.encode("utf-8"), "application/x-subrip")
        else:
            from shared.storage import r2 as real_r2  # noqa: PLC0415

            real_r2.put_object(audio_key, art.mp3_path.read_bytes(), "audio/mpeg")
            real_r2.put_object(mp4_key, art.mp4_path.read_bytes(), "video/mp4")
            real_r2.put_object(srt_key, art.srt.encode("utf-8"), "application/x-subrip")
    except Exception as exc:  # 包括 StorageError 與 MockR2 forced failure
        logger.warning(
            "upload_artifacts 失敗（%s），走本地 fallback episode_id=%s",
            exc,
            episode_id,
        )
        audio_key = mp4_key = srt_key = None  # type: ignore[assignment]
        storage_failed = True

    # 本地 fallback
    media_dir = settings.local_media_dir
    if media_dir and art.mp4_path.exists():
        try:
            from .mock import safe_local_fallback  # noqa: PLC0415

            safe_local_fallback(art.mp4_path, slug, media_dir)
        except OSError as exc:
            logger.warning("寫本地 fallback 失敗 %s: %s", slug, exc)

    # render_episode_node 用 make_job_workdir()（不會自動清）產出這些檔案，
    # 讀完（R2 上傳 + 本地 fallback 都做完）就是清掉的時機。
    if is_production:
        shutil.rmtree(art.mp3_path.parent, ignore_errors=True)

    return {
        "audio_key": audio_key,
        "mp4_key": mp4_key,
        "srt_key": srt_key,
        "storage_failed": storage_failed,
    }


# ── Node 8: update_episode_keys ───────────────────────────


async def update_episode_keys_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]
    art: EpisodeArtifacts = state["artifacts"]
    script: ScriptJSON = state["script"]
    # extracted_facts 現在是 SourcedFact 物件（非純字串），jsonb 落庫前先轉 dict。
    facts_payload = [f.model_dump(by_alias=False) for f in script.extracted_facts]

    if hasattr(repo, "update_episode_keys"):
        await repo.update_episode_keys(
            state["episode_id"],
            audio_key=state.get("audio_key"),
            mp4_key=state.get("mp4_key"),
            srt_key=state.get("srt_key"),
            script_json=script.model_dump(by_alias=False),
            cues=art.cues,
            extracted_facts=facts_payload,
            target_vocab=[v.model_dump(by_alias=False) for v in script.target_vocab],
        )
    else:
        from shared.db import repo as real_repo  # noqa: PLC0415

        await real_repo.update_episode_keys(
            state["episode_id"],
            audio_key=state.get("audio_key"),
            mp4_key=state.get("mp4_key"),
            srt_key=state.get("srt_key"),
            script_json=script.model_dump(by_alias=False),
            cues=art.cues,
            extracted_facts=facts_payload,
            target_vocab=[v.model_dump(by_alias=False) for v in script.target_vocab],
        )
    return {}


# ── Node 9: insert_deliveries ─────────────────────────────


async def insert_deliveries_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]

    user_ids: list[str] = state.get("user_ids") or []
    episode_id = state["episode_id"]
    deliver_date = state["deliver_date"]

    for uid in user_ids:
        if hasattr(repo, "insert_delivery"):
            await repo.insert_delivery(uid, episode_id, deliver_date)
        else:
            from shared.db import repo as real_repo  # noqa: PLC0415

            await real_repo.insert_delivery(uid, episode_id, deliver_date)

    return {}


# ── Node 10: backfill_dict（best-effort）─────────────────


async def backfill_dict_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """補缺字翻譯到 dict_translate queue。失敗不擋 generate。"""
    ctx = _ctx(config)
    queue_obj = ctx.get("queue")

    script: ScriptJSON | None = state.get("script")
    if script is None:
        return {}

    try:
        if queue_obj is not None:
            for v in script.target_vocab:
                await queue_obj.send(
                    "dict_translate",
                    {"word": v.word.casefold()},
                )
        else:
            from engine.pipeline.post_process import backfill_dict  # noqa: PLC0415

            await backfill_dict(script.target_vocab)
    except Exception as exc:
        logger.warning(
            "backfill_dict 失敗（不擋 generate）episode_id=%s: %s",
            state.get("episode_id"),
            exc,
        )

    return {}


# ── write_script 後的 rate-limit 路由 ──────────────────────


def rate_limit_decision(
    state: PodState, config: RunnableConfig
) -> Literal["failover", "judge", "__end__"]:
    """write_script_node 出來後的 conditional edge。

    rate_limited=False                              → judge
    rate_limited=True + failover_mode=failover       → failover_write_script
    rate_limited=True + failover_mode=degrade        → END（讓 worker 走 vt-retry）
    """
    settings = _ctx(config)
    if not state.get("rate_limited"):
        return "judge"
    if settings.get("failover_mode") == "failover" and settings.get("chat_failover") is not None:
        return "failover"
    return END  # type: ignore[return-value]


def failover_decision(state: PodState) -> Literal["judge", "__end__"]:
    if state.get("rate_limited"):
        return END  # type: ignore[return-value]
    return "judge"


# ── 確保 asyncio 在 mock 渲染的 sync 路徑下不卡 ────────────


async def _noop() -> None:
    await asyncio.sleep(0)
