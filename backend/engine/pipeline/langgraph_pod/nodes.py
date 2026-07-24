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
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from psycopg.errors import ForeignKeyViolation
from pydantic import ValidationError as PydanticValidationError

from engine.media import (
    EpisodeArtifacts,
    make_job_workdir,
    render_episode,
)
from engine.pipeline.langgraph_pod.prompt import (
    _strip_code_fence,
)
from shared.config import Settings
from shared.errors import GenerationError, RateLimitError, SourceFetchError
from shared.models import (
    JudgeVerdict,
    ScriptFormat,
    ScriptJSON,
    ScriptLine,
    ScriptOutline,
    SourcedFact,
    SourceSnippet,
)

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
# B1 = 190：三次真實渲染樣本（1015 字/333.15s≈182.8wpm、951 字/309.43s≈184.4wpm、
# 1003 字/303.19s≈198.5wpm）平均 ~188.6，取 190 略保守估；每次樣本都比上一版
# 校準值高，顯示先前的值持續低估——A2/B2 尚無真實樣本，維持原值待後續校準。
_CEFR_WPM: dict[str, int] = {"A2": 120, "B1": 190, "B2": 150}

# CEFR → 等級專屬寫作規範。沒有這塊時 A2 和 B2 拿到一模一樣的指令，
# 「分級」只剩字數差，聽者感受不到難度差異。
_CEFR_GUIDE: dict[str, str] = {
    "A2": (
        "Use ONLY high-frequency everyday words (roughly the 1,500 most common English words). "
        "Keep sentences under 12 words. Stick to present simple, past simple, and going-to "
        "future; avoid perfect tenses, passives, idioms, and phrasal verbs. "
        "If a harder word is unavoidable, explain it immediately in one short simple sentence."
    ),
    "B1": (
        "Use common everyday vocabulary (roughly the 3,000 most common English words), mostly "
        "simple sentences with some compound sentences. Explain any technical term on the spot "
        "in plain English. Use idioms sparingly and only with a quick natural explanation."
    ),
    "B2": (
        "Use natural, native-like vocabulary; idioms and phrasal verbs are welcome (briefly "
        "gloss only the rare ones). Vary sentence structure freely, but keep a natural spoken "
        "rhythm — this is audio, not an essay."
    ),
}


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


# 字數下限只用 length_tier 的「下限」分鐘數（不是上限）算：
# 實測 LLM 對字數目標系統性偏保守（785/1320=59%、1015/1320=77% vs. 上限目標），
# 逼近上限目標常需要無限重試都達不到；用下限當及格線，才是「這集至少要有下限長度」
# 這個真正在意的底線，可以透過重寫收斂到位。0.9 的折扣純粹是斷詞誤差的緩衝
# （text 斷詞用空白，連字號詞如 self-soothing 算一個 token，跟 wpm 的自然語速
# 假設本來就有些微落差）——不是刻意讓底線低於下限分鐘數，之前 0.85 的折扣太大，
# 就算 wpm 校準完全準確也會讓音檔系統性地比下限短 15%。
_MIN_LENGTH_FRACTION = 0.9


def _word_floor(cefr: str, length_tier: str) -> int:
    wpm = _CEFR_WPM.get(cefr, 140)
    lo, _ = _LENGTH_TIERS.get(length_tier, _LENGTH_TIERS["medium"])["minutes"]
    return int(wpm * lo * _MIN_LENGTH_FRACTION)


def _script_word_count(script: ScriptJSON) -> int:
    return sum(len(line.text.split()) for line in script.script)


def _segment_word_targets(cefr: str, length_tier: str) -> list[tuple[int, bool]]:
    """回傳 [(字數目標, is_chapter_boundary), ...]。

    短篇不分段（單發呼叫目標字數本來就小，分段只多一層合併風險）；
    中篇依內部切 3 段純粹控制字數，is_chapter_boundary 全 False（讀起來仍是單線
    推進，不出現 chapter 轉場語言）；長篇依 chapters 數，is_chapter_boundary 用
    True 對齊既有 _structure_block 對 long tier 的 chapter 轉場語意。

    每段目標字數加總精準等於 _word_target 上限（餘數併入最後一段），
    確保跟現有「整集字數目標」語意一致，不引入可見的長度漂移。
    """
    tier = _LENGTH_TIERS.get(length_tier, _LENGTH_TIERS["medium"])
    chapters = tier["chapters"]

    if chapters <= 1:
        # short / medium 都不分章。short 整集一發；medium 為了字數控制切成 3 段
        # 但關係圖讀起來仍是單線，所以 is_chapter_boundary=False。
        if length_tier == "short":
            return [(_word_target(cefr, length_tier), False)]
        n_segments = 3
        targets = [(_word_target(cefr, length_tier) // n_segments, False)] * n_segments
        # 餘數併入最後一段，避免加總漂移。
        remainder = _word_target(cefr, length_tier) - sum(t for t, _ in targets)
        targets[-1] = (targets[-1][0] + remainder, False)
        return targets

    # long tier：段數 = chapters，is_chapter_boundary 全 True。
    n_segments = chapters
    targets = [(_word_target(cefr, length_tier) // n_segments, True)] * n_segments
    remainder = _word_target(cefr, length_tier) - sum(t for t, _ in targets)
    targets[-1] = (targets[-1][0] + remainder, True)
    return targets


def _build_lemma_pool(text: str) -> set[str]:
    """共用 helper：把一段 text 斷詞、lemmatize、回傳所有候選 lemma 的集合。

    ScriptJSON._target_vocab_appears_in_script 與段落層級的 vocab 檢查都用這套
    規則，避免兩處邏輯漂移。
    """
    from shared.lemmatize import lemmatize  # noqa: PLC0415

    pool: set[str] = set()
    for token in re.findall(r"[A-Za-z']+", text.casefold()):
        pool.update(lemmatize(token))
    return pool
def _vocab_words_present(text: str, vocab_words: list[str]) -> list[str]:
    """回傳「在 text 裡沒出現（含詞形變化都沒有）」的 vocab_words。空 list = 全部命中。"""
    pool = _build_lemma_pool(text)
    missing: list[str] = []
    for w in vocab_words:
        w_lower = w.casefold()
        if " " in w_lower or "-" in w_lower:
            parts = [p for p in re.split(r"[\s-]+", w_lower) if p]
            if not all(p in pool for p in parts):
                missing.append(w)
        elif w_lower not in pool:
            missing.append(w)
    return missing


def _first_duplicate_adjacent_zh(lines: list[ScriptLine]) -> int | None:
    """回傳第一個跟前一行的 zh 完全相同的 index；None = 沒有重複。"""
    for i in range(1, len(lines)):
        if lines[i].zh == lines[i - 1].zh:
            return i
    return None


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
- 發言量要平衡：兩人輪流主導不同段落的講解，任一人總字數不得超過全片六成；\
不要一人講課、另一人只出短句捧哏（"Really?", "Weirder how?"）。
"""

_MONOLOGUE_VOICE = """
# SOLO NARRATOR VOICE（單人口白格式）
- 只有一個角色 Nova 對聽眾直接說話，沒有第二人聲可以互動，開場鉤子與節奏必須自己撐起來。
- 規律使用第二人稱直接對聽眾說話（"you"），並在每個轉場點加口語路標\
（"here's the thing", "let's back up", "so what does that actually mean"）。
"""

_FEW_SHOTS_DIALOGUE = """
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

_FEW_SHOTS_MONOLOGUE = """
# Few-shot exemplars（單人口白開場鉤子示範，非逐字模仿）

Example 1 (counter-intuitive stat):
Nova: Here's a number that shouldn't exist: emergency room visits went UP forty percent — \
right after people GOT health insurance. Stay with me, because the reason tells you \
everything about how incentives really work.

Example 2 (in medias res):
Nova: The server room went silent at 2:14 in the morning. Not quiet — silent. And for the \
engineers on call, silence was the worst sound in the world.
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
            f"- Body 拆成 {chapters} 個 chapter，每個從指定角度的不同切面推進"
            "（例如：具體案例→背後機制→常見誤解澄清→實際應用），"
            "各自有 hook→development→payoff 的小結構；"
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
        "（多出的時間用來對同一組字彙做不同語境的重複）。"
        "每個列進 target_vocab 的字都必須真的出現在對話 text 裡（含詞形變化，"
        "例如 escalate/escalated/escalating 算同一個字算出現過）；沒有真的用到"
        "就不要列進 target_vocab，這是硬性規則，會被程式檢查。\n"
        f"{chapter_line}\n{recap_line}"
    )


def _sources_block(sources: list[SourceSnippet], avoid_facts: tuple[str, ...] = ()) -> str:
    """把抓到的真實資料編號注入 prompt；空 sources 時退化成純 LLM 生成（沿用現況行為）。

    avoid_facts 同一份 SOURCES 常在同主題重生時被重新查到（搜尋查詢沒變），
    LLM 只看到「別用陳腔濫調開場」的泛用 BAN_LIST 很容易忽略事實層級的重複——
    在 extracted_facts 硬性規則旁邊直接重申 avoid_facts，才是真正擋下重複宣稱的地方。
    """
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
    if avoid_facts:
        lines.append(
            "\n以下事實舊集已經講過，即使 SOURCES 裡還查得到也不可以再放進 extracted_facts"
            "（換句話說改寫、同語意也算重複）；SOURCES 裡挑別的內容，真的沒有替代事實就少列一條："
        )
        lines.extend(f"- {f}" for f in avoid_facts)
    return "\n".join(lines)


def _shared_style_prefix(
    *,
    cefr: str,
    tone: str,
    format: ScriptFormat,
    avoid_facts: tuple[str, ...] = (),
) -> str:
    """outline / segment 兩個 builder 共用的前半段：角色設定 + 觀眾 + 必備風格。

    把 _build_pod_messages 原本一長串的常數拼裝共用、抽出來——同一份風格指令不要
    在兩個 builder 各自重複一份，免得哪邊改了另一邊沒跟上。HOOK / EXPLAINER /
    voice / BAN_LIST 是兩個 builder 都要的硬規範，集中在這裡；`length_tier` /
    `sources` / `structure_block` 是 outline/segment 各自專屬的組件，不放這裡。
    """
    tones_block = _TONE_BLOCKS.get(tone, _TONE_BLOCKS["playful"])
    cefr_guide = _CEFR_GUIDE.get(cefr, _CEFR_GUIDE["B1"])

    if format == "monologue":
        cast_line = "Write a solo narration by ONE host: Nova, speaking directly to the listener."
        voice_block = _MONOLOGUE_VOICE
    else:
        cast_line = "Write a natural, friendly conversation between TWO hosts: Alex and Sarah."
        voice_block = _DIALOGUE_CHEMISTRY

    avoid_lines: list[str] = []
    if avoid_facts:
        avoid_lines.append("Do NOT repeat these facts already covered:")
        avoid_lines.extend(f"- {f}" for f in avoid_facts)
    avoid_block = "\n".join(avoid_lines) if avoid_lines else "(none)"

    return (
        f"You are the head writer for DawnCast, a daily English-learning podcast. {cast_line}\n\n"
        f"# AUDIENCE & LEVEL\n- CEFR {cefr}. {cefr_guide}\n\n"
        f"{tones_block}\n"
        f"{_HOOK_TECHNIQUES}"
        f"{_EXPLAINER_SPINE}"
        f"{voice_block}"
        f"{_BAN_LIST.format(avoid_block=avoid_block)}"
    )


def _build_outline_messages(
    *,
    canonical_topic: str,
    big_topic: str,
    topic_type: str,
    angle: str,
    cefr: str,
    tone: str,
    length_tier: str,
    format: ScriptFormat,
    sources: list[SourceSnippet] | None,
    avoid_facts: tuple[str, ...],
    feedback: list[str] | None = None,
) -> list[dict[str, str]]:
    """大綱 LLM 呼叫：只規劃「哪些內容切到哪幾段、各段帶哪些字彙」，不寫對話。

    段落字數由 _segment_word_targets 算好直接餵給 prompt（避免 LLM 自己猜字數
    又系統性偏保守）。失敗重試走 _MAX_OUTLINE_RETRIES 固定 2 次（純 parse fix），
    耗盡才 raise 給外層 RetryPolicy；不佔用 _MAX_CONTRACT_RETRIES 額度。
    """
    targets = _segment_word_targets(cefr, length_tier)
    n = len(targets)

    bullets = "\n".join(
        (
            f"- Segment {i + 1}: about {t} English words, "
            + (
                "chapter boundary（前面加 transition 句，pause_before=true）"
                if boundary
                else "continue seamlessly from the previous segment"
            )
        )
        for i, (t, boundary) in enumerate(targets)
    )

    target_vocab_size = _LENGTH_TIERS.get(length_tier, _LENGTH_TIERS["medium"])["vocab"][1]
    avoid_lines = list(avoid_facts) if avoid_facts else []

    system = (
        _shared_style_prefix(
            cefr=cefr,
            tone=tone,
            format=format,
            avoid_facts=avoid_facts,
        )
        + "\n\n"
        + _structure_block(length_tier)
        + "\n\n"
        + f"# OUTLINE TASK\n"
        f"Plan this episode by splitting the content into exactly {n} segments.\n"
        f"Each segment's English word target is FIXED below（don't deviate）:\n"
        f"{bullets}\n\n"
        f"For each segment give a `focus` (1-2 sentences of what this segment covers)"
        f" and a `vocab_words` list (subset of target_vocab for this segment).\n"
        f"`vocab_words` 必須真的是 target_vocab 裡的字，不能憑主題聯想列字（這是硬性規則）。"
        f" 全集合計 target_vocab 數量上限 {target_vocab_size} 個。\n\n"
        f"# SOURCES\n{_sources_block(sources or [], avoid_facts)}\n\n"
        "JSON SCHEMA (must match exactly):\n"
        '{"topic": str, "topic_zh": str, '
        '"category": "tech"|"business"|"culture"|"science", '
        '"extracted_facts": [{"claim": str, "source_ids": [str]}], '
        '"target_vocab": [{"word": str, "explanation": str}], '
        f'"segments": [{{"focus": str, "vocab_words": [str]}}]}}\n'
        "Output ONLY the JSON object. No markdown, no code fences, no commentary."
    )

    user_parts = [
        "Plan today's episode.",
        f"- Canonical topic: {canonical_topic}",
        f"- Big topic: {big_topic}",
        f"- Topic type: {topic_type}",
        f"- Angle: {angle}",
    ]
    # avoid_facts 一定要在 system 裡也帶一次（不只是 user）：_sources_block 在
    # sources 空時就不輸出任何東西，避免只能靠 user 帶的「AVOID」段；
    # 之前修舊版 _build_pod_messages 那時就放過這個坑（commit 524 系），
    # 升級 outline builder 時這條規則沒自動繼承下去。
    if avoid_lines:
        user_parts.append("\nAVOID these facts (already covered in previous episodes):")
        user_parts.extend(f"- {f}" for f in avoid_lines)
    if feedback:
        user_parts.append("\nREVISION INSTRUCTIONS:")
        user_parts.extend(f"- {line}" for line in feedback)
        user_parts.append("\nRe-output the JSON outline.")
    user_parts.append("\nReturn the JSON object now.")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def _build_segment_messages(
    *,
    canonical_topic: str,
    big_topic: str,
    topic_type: str,
    angle: str,
    cefr: str,
    tone: str,
    length_tier: str,
    format: ScriptFormat,
    sources: list[SourceSnippet] | None,
    avoid_facts: tuple[str, ...],
    segment_index: int,
    segment_count: int,
    segment_focus: str,
    segment_vocab: list[str],
    segment_word_target: int,
    is_chapter_boundary: bool,
    is_final_segment: bool,
    previous_tail_lines: list[ScriptLine],
    extracted_facts: list[SourcedFact] | None = None,
    feedback: list[str] | None = None,
) -> list[dict[str, str]]:
    """單段擴寫 LLM 呼叫：只負責這段的對話內容，不重複 topic/vocab/facts。

    前後接續：帶上一段最後 2-3 行原文（不是整段全文，省 token）讓 LLM 從那裡
    繼續寫，避免話題/語氣斷裂或內容重複。中間段禁止「重新開場」、「總結轉場」；
    chapter boundary 段例外，保留既有 chapter 轉場語意。

    extracted_facts 從 outline 帶過來，讓這段寫稿不自創/扭曲已核准的事實；
    不帶時退化（舊呼叫端/Mock 測試可能不傳，行為不變）。
    """
    prev_tail = previous_tail_lines[-3:] if previous_tail_lines else []
    prev_text = "\n".join(f"{ln.speaker}: {ln.text}" for ln in prev_tail)

    if segment_index == 0:
        position_block = (
            "This is the **FIRST** segment of the episode. "
            "Open with one of the hook techniques (curiosity gap / in medias res / "
            "counter-intuitive stat / character-led). Do NOT use a generic intro."
        )
    elif is_final_segment:
        position_block = (
            "This is the **FINAL** segment. Land the payoff, then add a short recap "
            "of the main takeaway for the listener."
        )
    elif is_chapter_boundary:
        position_block = (
            "This is a **chapter boundary** segment. Start with a brief reset/transition "
            "sentence (回顧前段 + 一句話帶到這段), and set `pause_before: true` on the first line."
        )
    else:
        position_block = (
            "This is a **middle** segment. Continue directly from the previous segment's "
            "ending — do NOT re-open, do NOT summarize what came before, do NOT add a "
            "transition sentence. Just keep the conversation going."
        )

    schema_speaker = '"Nova"' if format == "monologue" else '"Alex"|"Sarah"'
    few_shots = _FEW_SHOTS_MONOLOGUE if format == "monologue" else _FEW_SHOTS_DIALOGUE

    system = (
        _shared_style_prefix(
            cefr=cefr,
            tone=tone,
            format=format,
            avoid_facts=avoid_facts,
        )
        + "\n\n"
        + "# BILINGUAL\n"
        "- Every line MUST have `zh` in natural Taiwan Mandarin (台灣正體中文), "
        "translate the meaning naturally, NOT word-for-word.\n"
        "- `zh` 只能翻譯「這一行自己的」`text`，禁止把下一行的內容提前挪進這一行的 zh，"
        "也禁止兩個連續行的 zh 一模一樣（這是程式會擋下來的硬性規則）。\n\n"
        "# SOURCES\n"
        f"{_sources_block(sources or [], avoid_facts)}\n\n"
        f"{few_shots}\n\n"
        "JSON SCHEMA (must match exactly, ONLY the script array):\n"
        '{"script": [{"speaker": ' + schema_speaker + ', "text": str, "zh": str, '
        '"pause_before": bool}]}\n'
        "Output ONLY the JSON object. No markdown, no code fences, no commentary."
    )

    user_parts = [
        f"Write segment {segment_index + 1} of {segment_count}.",
        f"- Canonical topic: {canonical_topic}",
        f"- Big topic: {big_topic}",
        f"- Topic type: {topic_type}",
        f"- Angle: {angle}",
        f"\n# THIS SEGMENT\n"
        f"- Focus: {segment_focus}\n"
        f"- Word target: ~{segment_word_target} English words (this segment only)\n"
        f"- Vocab words to weave in naturally: {segment_vocab or '(none)'}\n"
        f"\n# POSITION\n{position_block}\n",
    ]
    if extracted_facts:
        facts_lines = "\n".join(
            f"- {f.claim}" + (f" [{', '.join(f.source_ids)}]" if f.source_ids else "")
            for f in extracted_facts
        )
        user_parts.append(
            f"\n# APPROVED FACTS（這集大綱已核准的事實，腳本內容不得與其牴觸）\n{facts_lines}\n"
        )
    if prev_text:
        user_parts.append(
            f"\n# PREVIOUS SEGMENT (last 3 lines, continue from here):\n{prev_text}\n"
        )
    if feedback:
        user_parts.append("\nREVISION INSTRUCTIONS:")
        user_parts.extend(f"- {line}" for line in feedback)
        user_parts.append("\nRewrite this segment incorporating the above.")
    user_parts.append("\nReturn the JSON object now.")
    # few-shots 透過 system 帶在頂部不需要再進 user_parts（保持 user 乾淨）

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

# 兩層重試樹的兩個常數：
# - 大綱生成失敗（例如 LLM 沒按 schema）：純 parse fix，原地重試 2 次就夠，重多了
#   只是浪費 token；耗盡才 raise 給 graph.py 的 _WRITER_RETRY 當最後防線。
# - 整集合併後契約/字數沒過：大綱內容沒問題（內容規劃對），所以大綱不重打，
#   只重打段落並動態調高每段字數目標。原本 5 次：分段後 LLM 對單段目標更容易
#   達標，預期 Level 1 段落內重試擋掉大部分修法，Level 2 觸發頻率會明顯降，
#   拉回 3 次跟使用者「不用省 token」但避免無謂燒錢的平衡點。
_MAX_OUTLINE_RETRIES = 2
_MAX_CONTRACT_RETRIES = 3
# 段落層級 vocab/zh 修不好的額外重打上限。Level 1 是便宜的重試（單段而已），
# 給 2 次額度讓 LLM 有機會自己修正；超過就 raise 出去，讓 Level 2 整輪重打。
_MAX_SEGMENT_RETRIES = 2


def _parse_outline(raw_text: str) -> ScriptOutline:
    """剝 code fence → 驗證成 ScriptOutline。

    結構性失敗（schema 不符 / vocab_words 不在 target_vocab 裡）一律 raise
    GenerationError，讓 _invoke_writer 觸發 _MAX_OUTLINE_RETRIES 級重試。
    不負責量測 token usage（由 caller 累加）。
    """
    cleaned = _strip_code_fence(raw_text)
    try:
        return ScriptOutline.model_validate_json(cleaned)
    except (PydanticValidationError, json.JSONDecodeError) as exc:
        raise GenerationError(f"大綱回應無法解析成合法 ScriptOutline：{exc}") from exc


def _parse_segment_script(raw_text: str) -> list[ScriptLine]:
    """剝 code fence → 解出這一段的 script 陣列。

    結構性失敗一律 raise GenerationError。返回 list[ScriptLine] 而非 ScriptJSON
    是因為段落層級只在意對話本身，topic/category/vocab 那些在大綱定案。
    """
    cleaned = _strip_code_fence(raw_text)
    try:
        data = json.loads(cleaned)
        script_data = data["script"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GenerationError(f"段落回應不是合法 JSON 物件含 script 陣列：{exc}") from exc
    try:
        return [ScriptLine.model_validate(ln) for ln in script_data]
    except PydanticValidationError as exc:
        raise GenerationError(f"段落 script 行驗證失敗：{exc}") from exc


def _merge_outline_and_segments(
    outline: ScriptOutline,
    segment_scripts: list[list[ScriptLine]],
    fmt: ScriptFormat,
) -> ScriptJSON:
    """把大綱的 metadata + 各段 script 合併成完整 ScriptJSON。

    走 full ScriptJSON.model_validate，所以 _speakers_match_format /
    _no_duplicate_adjacent_zh / _target_vocab_appears_in_script 三個既有
    validator 會自動對合併後的全文跑一次，天然接住段落邊界交界處的問題
    （例如邊界兩行 zh 恰好重複、整集合併後 vocab 才命中）。
    """
    if len(segment_scripts) != len(outline.segments):
        raise ValueError(
            f"段落數量 {len(segment_scripts)} 與大綱 segments {len(outline.segments)} 不符"
        )
    merged_lines: list[ScriptLine] = []
    for seg_lines in segment_scripts:
        merged_lines.extend(seg_lines)
    payload = {
        "topic": outline.topic,
        "topic_zh": outline.topic_zh,
        "category": outline.category,
        "extracted_facts": [f.model_dump() for f in outline.extracted_facts],
        "target_vocab": [v.model_dump() for v in outline.target_vocab],
        "script": [ln.model_dump() for ln in merged_lines],
        "format": fmt,
    }
    return ScriptJSON.model_validate(payload)


async def _generate_outline(
    chat: Any,
    state: PodState,
    settings: Settings,
    *,
    engine_label: str,
    usage_node: str,
    cefr: str,
    length_tier: str,
    feedback: list[str] | None = None,
) -> tuple[ScriptOutline, dict[str, Any], dict[str, int]]:
    """打 LLM 產大綱。回傳 (outline, usage_metadata_by_call, total_usage)。

    失敗重試 _MAX_OUTLINE_RETRIES 次（純 parse fix），耗盡 raise GenerationError
    給外層 RetryPolicy 接手。RateLimitError 改回傳特殊 sentinel 給 caller 路由。
    """
    msgs = _build_outline_messages(
        canonical_topic=state["canonical_topic"],
        big_topic=state["big_topic"],
        topic_type=state["topic_type"],
        angle=state["angle"],
        cefr=cefr,
        tone=state.get("tone", "playful"),
        length_tier=length_tier,
        format=state.get("format", "dialogue"),
        sources=state.get("sources"),
        avoid_facts=tuple(state.get("avoid_facts") or ()),
        feedback=feedback,
    )

    last_exc: GenerationError | None = None
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    for attempt in range(_MAX_OUTLINE_RETRIES + 1):
        try:
            ai_msg = await chat.ainvoke(_to_lc_messages(msgs))
        except RateLimitError:
            logger.warning("%s 撞限流 big_topic=%s (outline)", usage_node, state["big_topic"])
            raise

        usage = _usage_from_ai_msg(ai_msg)
        total_usage["input_tokens"] += cast(int, usage.get("input_tokens", 0))
        total_usage["output_tokens"] += cast(int, usage.get("output_tokens", 0))

        try:
            return _parse_outline(ai_msg.content), usage, total_usage
        except GenerationError as exc:
            last_exc = exc
            logger.warning(
                "%s outline 第 %d/%d 次解析失敗 big_topic=%s: %s",
                usage_node,
                attempt + 1,
                _MAX_OUTLINE_RETRIES + 1,
                state["big_topic"],
                exc,
            )
            if attempt < _MAX_OUTLINE_RETRIES:
                msgs = _build_outline_messages(
                    canonical_topic=state["canonical_topic"],
                    big_topic=state["big_topic"],
                    topic_type=state["topic_type"],
                    angle=state["angle"],
                    cefr=cefr,
                    tone=state.get("tone", "playful"),
                    length_tier=length_tier,
                    format=state.get("format", "dialogue"),
                    sources=state.get("sources"),
                    avoid_facts=tuple(state.get("avoid_facts") or ()),
                    feedback=[f"上一版大綱無法解析成合法結構：{exc}"],
                )
                continue
    assert last_exc is not None
    raise last_exc


async def _generate_segment(  # type: ignore[return]
    chat: Any,
    state: PodState,
    settings: Settings,
    *,
    engine_label: str,
    usage_node: str,
    cefr: str,
    length_tier: str,
    segment_index: int,
    segment_count: int,
    segment_focus: str,
    segment_vocab: list[str],
    segment_word_target: int,
    is_chapter_boundary: bool,
    is_final_segment: bool,
    previous_tail_lines: list[ScriptLine],
    extracted_facts: list[SourcedFact] | None = None,
    feedback: list[str] | None = None,
) -> tuple[list[ScriptLine], dict[str, int]]:
    """打 LLM 寫單段對話。回傳 (script_lines, total_usage_for_this_segment)。

    Level 1 段落內重試：每段生成完立刻做《段內 vocab 命中 + 段內 zh 不重複》
    兩項檢查，沒過帶著具體錯誤內容重打這一段，上限 _MAX_SEGMENT_RETRIES 次。
    RateLimitError 讓 _invoke_writer 整段路由（不 raise 自身）。
    """
    fmt = state.get("format", "dialogue")
    msgs = _build_segment_messages(
        canonical_topic=state["canonical_topic"],
        big_topic=state["big_topic"],
        topic_type=state["topic_type"],
        angle=state["angle"],
        cefr=cefr,
        tone=state.get("tone", "playful"),
        length_tier=length_tier,
        format=fmt,
        sources=state.get("sources"),
        avoid_facts=tuple(state.get("avoid_facts") or ()),
        segment_index=segment_index,
        segment_count=segment_count,
        segment_focus=segment_focus,
        segment_vocab=segment_vocab,
        segment_word_target=segment_word_target,
        is_chapter_boundary=is_chapter_boundary,
        is_final_segment=is_final_segment,
        previous_tail_lines=previous_tail_lines,
        extracted_facts=extracted_facts,
        feedback=feedback,
    )

    total_usage = {"input_tokens": 0, "output_tokens": 0}
    last_exc: GenerationError | None = None
    for attempt in range(_MAX_SEGMENT_RETRIES + 1):
        try:
            ai_msg = await chat.ainvoke(_to_lc_messages(msgs))
        except RateLimitError:
            logger.warning(
                "%s 撞限流 big_topic=%s (segment %d/%d)",
                usage_node,
                state["big_topic"],
                segment_index + 1,
                segment_count,
            )
            raise

        usage = _usage_from_ai_msg(ai_msg)
        total_usage["input_tokens"] += cast(int, usage.get("input_tokens", 0))
        total_usage["output_tokens"] += cast(int, usage.get("output_tokens", 0))

        try:
            lines = _parse_segment_script(ai_msg.content)
        except GenerationError as exc:
            last_exc = exc
            logger.warning(
                "%s segment %d/%d 第 %d 次解析失敗 big_topic=%s: %s",
                usage_node,
                segment_index + 1,
                segment_count,
                attempt + 1,
                state["big_topic"],
                exc,
            )
            if attempt < _MAX_SEGMENT_RETRIES:
                msgs = _build_segment_messages(
                    canonical_topic=state["canonical_topic"],
                    big_topic=state["big_topic"],
                    topic_type=state["topic_type"],
                    angle=state["angle"],
                    cefr=cefr,
                    tone=state.get("tone", "playful"),
                    length_tier=length_tier,
                    format=fmt,
                    sources=state.get("sources"),
                    avoid_facts=tuple(state.get("avoid_facts") or ()),
                    segment_index=segment_index,
                    segment_count=segment_count,
                    segment_focus=segment_focus,
                    segment_vocab=segment_vocab,
                    segment_word_target=segment_word_target,
                    is_chapter_boundary=is_chapter_boundary,
                    is_final_segment=is_final_segment,
                    previous_tail_lines=previous_tail_lines,
                    extracted_facts=extracted_facts,
                    feedback=[f"上一版這段 JSON 解析失敗：{exc}"],
                )
                continue
            raise

        # Level 1 段落內檢查：vocab 命中 + 段內 zh 不重複。
        seg_text = " ".join(ln.text for ln in lines)
        missing_vocab = _vocab_words_present(seg_text, segment_vocab)
        dup_idx = _first_duplicate_adjacent_zh(lines)
        if not missing_vocab and dup_idx is None:
            return lines, total_usage

        feedback_msgs: list[str] = []
        if missing_vocab:
            feedback_msgs.append(
                f"這段應該用到的 vocab 沒真的寫進對話（含詞形變化都沒有）：{missing_vocab}"
            )
        if dup_idx is not None:
            feedback_msgs.append(
                f"script[{dup_idx}] 與 script[{dup_idx - 1}] 的 zh 完全相同"
                "（中英對齊漂移，必須重寫）"
            )
        logger.warning(
            "%s segment %d/%d Level 1 檢查失敗 big_topic=%s: %s",
            usage_node,
            segment_index + 1,
            segment_count,
            state["big_topic"],
            feedback_msgs,
        )
        last_exc = GenerationError(f"段落 {segment_index + 1} 段內契約失敗：{feedback_msgs}")
        if attempt < _MAX_SEGMENT_RETRIES:
            msgs = _build_segment_messages(
                canonical_topic=state["canonical_topic"],
                big_topic=state["big_topic"],
                topic_type=state["topic_type"],
                angle=state["angle"],
                cefr=cefr,
                tone=state.get("tone", "playful"),
                length_tier=length_tier,
                format=fmt,
                sources=state.get("sources"),
                avoid_facts=tuple(state.get("avoid_facts") or ()),
                segment_index=segment_index,
                segment_count=segment_count,
                segment_focus=segment_focus,
                segment_vocab=segment_vocab,
                segment_word_target=segment_word_target,
                is_chapter_boundary=is_chapter_boundary,
                is_final_segment=is_final_segment,
                previous_tail_lines=previous_tail_lines,
                extracted_facts=extracted_facts,
                feedback=feedback_msgs,
            )
            continue
        # mypy 不追蹤「for 跑完 last_exc 必非 None」這個 invariant——assert 在 strict
        # 模式不會 narrow，raise last_exc 在 mypy 看來仍可能為 None。
        assert last_exc is not None
        raise last_exc


async def _invoke_writer(
    chat: Any, state: PodState, settings: Settings, *, engine_label: str, usage_node: str
) -> dict[str, Any]:
    """primary / failover 共用的寫稿呼叫：outline 1 次 + 分段 N 次。

    兩層重試樹：
      Level 1（段落內，便宜）：每段生成完立刻檢查 vocab 命中 + 段內 zh 不重複，
        沒過重打「這一段」最多 _MAX_SEGMENT_RETRIES 額度。
      Level 2（合併後，整集）：所有段落過關後合併成 ScriptJSON，走現有三個
        model_validator；任一沒過或總字數 < word_floor → 重打「全部段落」
        最多 _MAX_CONTRACT_RETRIES 額度，並把每段字數目標依短缺比例調高
        （大綱不重打，內容規劃對，只是字數擴寫沒到位）。

    RateLimitError 任何階段都 raise 給 _invoke_writer 入口的 try/except 接住，
    回 rate_limited=True 給 conditional edge 路由。

    額度用完仍偏短 → 用歷來最長的一版出稿（best-draft fallback），不 raise
    讓整集生成失敗（字數是軟性品質目標）。
    """
    base_feedback = list(state.get("judge_feedback") or [])
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    cefr = state.get("cefr") or settings.cefr_level
    length_tier = state.get("length_tier") or "medium"
    fmt = state.get("format", "dialogue")
    word_floor = _word_floor(cefr, length_tier)

    best_result: ScriptJSON | None = None
    best_word_count = -1

    # 第一階段：生大綱（純結構性 parse fix，重試極少）
    outline: ScriptOutline | None = None
    try:
        outline, _, outline_usage = await _generate_outline(
            chat,
            state,
            settings,
            engine_label=engine_label,
            usage_node=usage_node,
            cefr=cefr,
            length_tier=length_tier,
            feedback=base_feedback or None,
        )
    except RateLimitError:
        return {"rate_limited": True, "engine_used": engine_label}
    total_usage["input_tokens"] += outline_usage["input_tokens"]
    total_usage["output_tokens"] += outline_usage["output_tokens"]
    assert outline is not None

    # 第二階段：分段 → 合併 → Level 2 驗證，重打可調整每段字數目標
    adjuster = 1.0
    last_exc: GenerationError | None = None
    word_total = _word_target(cefr, length_tier)
    # 段落分配基準：均分 word_total 到 outline.segments，每段都標 boundary=False（這裡
    # 只管字數；chaper 邊界語意由 prompt 端 is_chapter_boundary 處理，底下 round 內
    # 依 long tier 決定）。sections 數量由 outline 決定，不寫死。
    n_segments = len(outline.segments)
    base_segment_words = [(max(1, word_total // n_segments), False)] * n_segments
    base_segment_words[-1] = (
        base_segment_words[-1][0] + (word_total - word_total // n_segments * n_segments),
        False,
    )
    # long tier 把所有段都標成 chapter boundary（_build_segment_messages 看 is_chapter_boundary
    # 決定要不要加 transition + pause_before）。
    if length_tier == "long":
        base_segment_words = [(w, True) for w, _ in base_segment_words]

    for round_idx in range(_MAX_CONTRACT_RETRIES):
        targets = [
            (max(1, int(w * adjuster)), boundary) for (w, boundary) in base_segment_words
        ]
        # 動態調高後仍維持加總對齊到 word_total（不引入可見長度漂移）。
        # 但 adjuster > 1 時 sum 已經超過 word_total，剩餘量為負，硬補會把最後一段
        # 變成負數（實測 adjuster=2 → 末段變 -504，_generate_segment 那邊 max(1,…) 才
        # 救回，但 prompt 看到的負 target 已經失真）。改寫：remainder 不夠時寧可放寬
        # 對齊約束，避免污染 LLM 指令。
        remainder = word_total - sum(t for t, _ in targets)
        if remainder < 0:
            # 整體已超過 word_total：不再往下扣，overshoot 是「每段目標拉高後」的
            # 副作用，可接受；總字數上限本來就在 ScriptJSON 走合併後實際計算，不靠這個對齊。
            pass
        elif remainder > 0:
            t_list = [t for t, _ in targets]
            t_list[-1] += remainder
            targets = list(zip(t_list, [b for _, b in targets], strict=True))

        segment_scripts: list[list[ScriptLine]] = []
        try:
            for seg_idx, (outline_seg, (target_words, boundary)) in enumerate(
                zip(outline.segments, targets, strict=True)
            ):
                prev_tail = segment_scripts[-1][-3:] if segment_scripts else []
                lines, seg_usage = await _generate_segment(
                    chat,
                    state,
                    settings,
                    engine_label=engine_label,
                    usage_node=usage_node,
                    cefr=cefr,
                    length_tier=length_tier,
                    segment_index=seg_idx,
                    segment_count=len(outline.segments),
                    segment_focus=outline_seg.focus,
                    segment_vocab=outline_seg.vocab_words,
                    segment_word_target=target_words,
                    is_chapter_boundary=boundary,
                    is_final_segment=(seg_idx == len(outline.segments) - 1),
                    previous_tail_lines=prev_tail,
                    extracted_facts=outline.extracted_facts,
                    feedback=base_feedback or None,
                )
                total_usage["input_tokens"] += seg_usage["input_tokens"]
                total_usage["output_tokens"] += seg_usage["output_tokens"]
                segment_scripts.append(lines)
        except RateLimitError:
            return {"rate_limited": True, "engine_used": engine_label}
        except GenerationError as exc:
            last_exc = exc
            logger.warning(
                "%s 第 %d/%d 輪段落內契約失敗 big_topic=%s: %s",
                usage_node,
                round_idx + 1,
                _MAX_CONTRACT_RETRIES,
                state["big_topic"],
                exc,
            )
            adjuster *= 1.1
            continue

        # Level 2：合併後跑 ScriptJSON 三個 validator + 字數 floor
        try:
            full_script = _merge_outline_and_segments(outline, segment_scripts, fmt)
        except (PydanticValidationError, ValueError) as exc:
            last_exc = GenerationError(f"合併後 ScriptJSON 違反契約：{exc}")
            logger.warning(
                "%s 第 %d/%d 輪合併驗證失敗 big_topic=%s: %s",
                usage_node,
                round_idx + 1,
                _MAX_CONTRACT_RETRIES,
                state["big_topic"],
                last_exc,
            )
            adjuster *= 1.1
            continue

        word_count = _script_word_count(full_script)
        if word_count > best_word_count:
            best_result, best_word_count = full_script, word_count

        if word_count >= word_floor:
            return {
                "script": full_script,
                "engine_used": engine_label,
                "rate_limited": False,
                "token_usage": [{"node": usage_node, **total_usage}],
            }

        # 字數不足是軟性品質目標，不是硬契約：最後一輪用最長的出稿（fallback）。
        is_last_round = round_idx == _MAX_CONTRACT_RETRIES - 1
        if is_last_round:
            assert best_result is not None
            if best_word_count < word_floor:
                logger.warning(
                    "%s 字數重試耗盡仍偏短 big_topic=%s: %d words（floor=%d），用最長一版出稿",
                    usage_node,
                    state["big_topic"],
                    best_word_count,
                    word_floor,
                )
            return {
                "script": best_result,
                "engine_used": engine_label,
                "rate_limited": False,
                "token_usage": [{"node": usage_node, **total_usage}],
            }

        logger.warning(
            "%s 第 %d/%d 輪合併後字數不足 big_topic=%s: %d words（floor=%d），"
            "下一輪每段字數目標 ×%.2f",
            usage_node,
            round_idx + 1,
            _MAX_CONTRACT_RETRIES,
            state["big_topic"],
            word_count,
            word_floor,
            adjuster * 1.1,
        )
        # 短缺比例 × 1.2 給保險係數（避免連續小幅短缺浪費回合）。
        ratio = (word_floor / max(word_count, 1)) * 1.2
        adjuster = max(adjuster, ratio)
        last_exc = GenerationError(
            f"整集 {word_count} 字低於 {word_floor} 下限"
        )

    # 全部回合都因契約違規失敗（不是字數不足），用最長的一版出稿（best-draft fallback）。
    # 若 best_result 仍是 None 代表沒任何一輪過 Level 2 合併驗證——沒有「最長的一版」
    # 可出，這時 raise last_exc 給 RetryPolicy 走 vt-retry 比塞空稿合理。
    if best_result is None:
        assert last_exc is not None
        logger.error(
            "%s 重試耗盡且無 best-draft 可用 big_topic=%s: %s",
            usage_node,
            state["big_topic"],
            last_exc,
        )
        raise last_exc
    logger.warning(
        "%s 重試耗盡仍契約失敗 big_topic=%s（last: %s），用最長一版出稿",
        usage_node,
        state["big_topic"],
        last_exc,
    )
    return {
        "script": best_result,
        "engine_used": engine_label,
        "rate_limited": False,
        "token_usage": [{"node": usage_node, **total_usage}],
    }


async def write_script_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """打 LLM 寫稿。RateLimitError → 設 rate_limited=True，不 raise。"""
    ctx = _ctx(config)
    return await _invoke_writer(
        ctx["chat"],
        state,
        ctx["settings"],
        engine_label="primary",
        usage_node="write_script",
    )


# ── Node 3: failover_write_script ────────────────────────


async def failover_write_script_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """failover_mode=failover 時由 conditional edge 路由過來，用 chat_failover 重打一次。"""
    ctx = _ctx(config)
    chat = ctx.get("chat_failover")
    if chat is None:
        return {
            "rate_limited": False,
            "errors": ["failover requested but no chat_failover configured"],
        }

    out = await _invoke_writer(
        chat,
        state,
        ctx["settings"],
        engine_label="failover",
        usage_node="write_script_failover",
    )
    if out.get("rate_limited"):
        out["errors"] = ["failover engine also rate-limited"]
    return out


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
    cefr = state.get("cefr") or "B1"
    source = state.get("source") or "fallback"
    is_free = source != "specified"

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

    # repo 是 MockRepo 或 shared.db.repo 模組，surface 相同——直接呼叫，不做 hasattr 分派。
    episode_id, already_rendered = await repo.upsert_episode(
        idempotency_key=idem_key,
        slug=slug,
        title=script.topic,
        topic=script.category,
        big_topic=big_topic,
        angle=angle,
        topic_type=state["topic_type"],
        cefr_level=cefr,
        title_zh=script.topic_zh,
        cluster_id=cluster_id,
        length_tier=length_tier,
        format=script_format,
        grounded=grounded,
        input_tokens=total_in,
        output_tokens=total_out,
        is_free=is_free,
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

    return {
        "episode_id": episode_id,
        "slug": slug,
        "idempotency_key": idem_key,
        "already_rendered": already_rendered,
    }


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
        mp3, srt, cues = renderer.render(script_payload)
        return {
            "artifacts": EpisodeArtifacts(
                mp3_path=mp3,
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
    artifacts = await render_episode(script, workdir, cefr=state.get("cefr") or "B1")
    return {"artifacts": artifacts}


# ── Node 7: upload_artifacts ──────────────────────────────


def storage_decision(
    state: PodState, config: RunnableConfig
) -> Literal["update_keys", "dead_letter"]:
    """upload_artifacts 後分流：本地 fallback 也沒救 → dead_letter_node → END。

    條件：storage_failed AND 沒有本機 mp3 fallback → 不能留半完成 row
    （播放頁會 404），也不能 raise（會觸發 worker pgmq vt 重投 → render
    整個重做）。改成 graceful END：decision 走 dead_letter_node 做
    DELETE + 寫 errors，worker 視為完成，read_ct 不累積。

    其他情況（r2 OK / r2 失敗但本地 fallback 成功）→ 走 update_episode_keys
    + insert_deliveries，前端可從本地路徑或 R2 取音檔。
    """
    if not state.get("storage_failed"):
        return "update_keys"
    settings = _ctx(config).get("settings")
    media_dir = getattr(settings, "local_media_dir", None)
    slug = state.get("slug")
    if media_dir and slug:
        local_mp3 = Path(media_dir, f"{slug}.mp3")
        if local_mp3.is_file():
            return "update_keys"
    return "dead_letter"


async def dead_letter_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """storage_failed + 無本地 fallback → DELETE 半完成 row，graceful END。

    取代原本 update_episode_keys_node 在這情況 raise RuntimeError 的行為：
    raise 會觸發 LangGraph 整個 invoke 失敗 → worker pgmq vt 重投 → 整集
    render_episode (TTS 33s+) 重做。改 graceful END：DELETE row + 寫
    errors 標記，worker 視為完成，read_ct 不累積。
    """
    repo = _ctx(config).get("repo")
    idem_key = state.get("idempotency_key")
    slug = state.get("slug")
    episode_id = state.get("episode_id")
    if repo is not None and idem_key:
        await repo.delete_episode_by_idem(idem_key)
    logger.warning(
        "媒體雙重失敗 graceful dead-letter（id=%s slug=%s idem=%s）",
        episode_id,
        slug,
        idem_key,
    )
    return {
        "errors": [
            *state.get("errors", []),
            f"upload_artifacts 雙重失敗，row 已清。slug={slug}",
        ]
    }


async def upload_artifacts_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    r2 = ctx.get("r2")
    settings: Settings = ctx["settings"]

    episode_id = state["episode_id"]
    slug = state["slug"]
    art: EpisodeArtifacts = state["artifacts"]

    prefix = f"episodes/{episode_id}"
    audio_key = f"{prefix}/episode.mp3"
    srt_key = f"{prefix}/episode.srt"

    is_production = r2 is None  # mock 路徑會注入 MockR2；production 沒有才走真 R2

    storage_failed = False
    try:
        if r2 is not None:
            r2.put_object(audio_key, art.mp3_path.read_bytes(), "audio/mpeg")
            r2.put_object(srt_key, art.srt.encode("utf-8"), "application/x-subrip")
        else:
            from shared.storage import r2 as real_r2  # noqa: PLC0415

            real_r2.put_object(audio_key, art.mp3_path.read_bytes(), "audio/mpeg")
            real_r2.put_object(srt_key, art.srt.encode("utf-8"), "application/x-subrip")
    except Exception as exc:  # 包括 StorageError 與 MockR2 forced failure
        logger.warning(
            "upload_artifacts 失敗（%s），走本地 fallback episode_id=%s",
            exc,
            episode_id,
        )
        audio_key = srt_key = None  # type: ignore[assignment]
        storage_failed = True

    # 本地 fallback
    media_dir = settings.local_media_dir
    if media_dir and art.mp3_path.exists():
        try:
            from .mock import safe_local_fallback  # noqa: PLC0415

            safe_local_fallback(art.mp3_path, slug, media_dir)
        except OSError as exc:
            logger.warning("寫本地 fallback 失敗 %s: %s", slug, exc)

    # render_episode_node 用 make_job_workdir()（不會自動清）產出這些檔案，
    # 讀完（R2 上傳 + 本地 fallback 都做完）就是清掉的時機。
    if is_production:
        shutil.rmtree(art.mp3_path.parent, ignore_errors=True)

    return {
        "audio_key": audio_key,
        "srt_key": srt_key,
        "storage_failed": storage_failed,
    }


# ── Node 8: update_episode_keys ───────────────────────────


async def update_episode_keys_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]
    settings: Settings = ctx["settings"]
    art: EpisodeArtifacts = state["artifacts"]
    script: ScriptJSON = state["script"]
    # extracted_facts 現在是 SourcedFact 物件（非純字串），jsonb 落庫前先轉 dict。
    facts_payload = [f.model_dump(by_alias=False) for f in script.extracted_facts]

    # ponytail：媒體落地保護。R2 失敗 + 本機 fallback 也沒檔 → 不能留 row，
    # 否則播放頁會 404（DB row 有 script_json、無 audio）— DELETE 該 row 並 raise，
    # 讓 _process 走 retry / dead-letter，不留沒聲音的殭屍集數。
    slug = state["slug"]
    storage_failed = bool(state.get("storage_failed"))
    media_dir = settings.local_media_dir
    local_mp3 = Path(media_dir, f"{slug}.mp3") if media_dir else None
    no_local = local_mp3 is None or not local_mp3.is_file()
    if storage_failed and no_local:
        episode_id = state["episode_id"]
        idem_key = state["idempotency_key"]
        logger.warning(
            "媒體雙重失敗，刪除半完成 row（id=%s slug=%s idem=%s），graceful END",
            episode_id,
            slug,
            idem_key,
        )
        await repo.delete_episode_by_idem(idem_key)
        # 上層 graph 已 conditional 分流到 END；這裡再寫一次 errors 是防呆，
        # 確保即使 conditional edge 未來被改壞也不會觸發 worker pgmq vt 重投 → render 重做。
        return {
            "errors": [
                *state.get("errors", []),
                f"upload_artifacts 雙重失敗，row 已清。slug={slug}",
            ]
        }

    # repo 是 MockRepo 或 shared.db.repo 模組，surface 相同——直接呼叫，不做 hasattr 分派。
    await repo.update_episode_keys(
        state["episode_id"],
        audio_key=state.get("audio_key"),
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
        try:
            await repo.insert_delivery(uid, episode_id, deliver_date)
        except ForeignKeyViolation:
            # 上游補償（update_episode_keys_node 的 DELETE-on-failure 或 worker
            # _compensate_generate_failure）已把這筆 episode row 刪掉 —
            # 沒對應 row 就沒人可以交付，當作「這集本輪失敗、不交付」，
            # 不讓 FK violation 終止整個 graph（否則 graph 失敗 → worker 走
            # vt-retry → 又卡同一個 FK → 死循環）。
            logger.warning(
                "insert_delivery 找不到對應 episode（id=%s uid=%s），上輪補償刪掉了，略過",
                episode_id,
                uid,
            )

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
