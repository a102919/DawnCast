"""Writer 階段 nodes：outline → per-segment 擴寫 → 行長正規化 → 合併成整集腳本。

兩層重試樹：大綱 parse fix（`_MAX_OUTLINE_RETRIES`）跟整集契約/字數沒過的段落級
重打（`_MAX_CONTRACT_RETRIES`），細節見 `_invoke_writer` docstring。
"""

from __future__ import annotations

import functools
import json
import logging
import re
import time
from itertools import pairwise
from typing import Any, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from pydantic import ValidationError as PydanticValidationError

from engine.pipeline.langgraph_pod.prompt import _strip_code_fence
from shared.config import Settings
from shared.errors import GenerationError, RateLimitError
from shared.models import (
    ScriptFormat,
    ScriptJSON,
    ScriptLine,
    ScriptOutline,
    SourcedFact,
    SourceSnippet,
    VerifiedClaim,
)
from shared.script_contract import first_duplicate_adjacent_index, missing_vocab_words

from .metrics import MetricsCollector
from .nodes_common import _collector, _ctx, _record_llm_usage
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
        "in plain English. Use idioms sparingly and only with a quick natural explanation. "
        "Keep each line under 12 words — every line is displayed next to its Chinese "
        "translation, so long lines break the word-by-word alignment learners rely on."
    ),
    "B2": (
        "Use natural, native-like vocabulary; idioms and phrasal verbs are welcome (briefly "
        "gloss only the rare ones). Vary sentence structure freely, but keep a natural spoken "
        "rhythm — this is audio, not an essay. Keep each line under 12 words — every line is "
        "displayed next to its Chinese translation; break longer thoughts across several lines."
    ),
}


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
- 英文句子自然長度即可，無需刻意切短；text 超過 12 詞的行會由後處理自動切分為 2-3 段。
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
# Few-shot exemplars（開場鉤子示範，非逐字模仿；每行後面標的是該行 emotion）

Example 1 (curiosity gap, topic="量子力學"):
Alex: You know that feeling when headphones go on, the world just... disappears? [neutral]
Sarah: Mmm. [neutral]
Alex: Imagine that, but for an electron. The electron can't take the headphones off. [surprised]

Example 2 (character-led, topic="投資組合"):
Sarah: My uncle once put all his savings into one stock. One stock, Alex. [surprised]
Alex: And? [neutral]
Sarah: Let's say he's now a very enthusiastic fan of... index funds. [happy]

Example 3 (counter-intuitive stat, topic="remote work"):
Alex: Companies that went fully remote saw output go UP, not down. Nobody predicted that. \
[surprised]
Sarah: Wait, really? Everyone I know assumed the opposite. [surprised]
"""

_FEW_SHOTS_MONOLOGUE = """
# Few-shot exemplars（單人口白開場鉤子示範，非逐字模仿；每行後面標的是該行 emotion）

Example 1 (counter-intuitive stat):
Nova: Here's a number that shouldn't exist: emergency room visits went UP forty percent — \
right after people GOT health insurance. Stay with me, because the reason tells you \
everything about how incentives really work. [surprised]

Example 2 (in medias res):
Nova: The server room went silent at 2:14 in the morning. Not quiet — silent. And for the \
engineers on call, silence was the worst sound in the world. [fearful]
"""

_EMOTION_GUIDE = """
# EMOTION（逐行標，MiniMax TTS 用來調語氣，不要整段都同一個值）
- 每行 JSON 都要有 `emotion`，只能是以下 7 個值之一：
  happy / sad / angry / fearful / disgusted / surprised / neutral。
- 預設 neutral；hook、反直覺轉折、驚訝發現 → happy/surprised；
  立場分歧、輕度反駁 → angry（輕度用，不要每次分歧都套）；
  深沉話題、recap、收尾洞察 → neutral/sad；
  fearful/disgusted 少用，只在內容本身談風險/負面案例時才用。
- `text` 裡不要用 `...`（刪節號）表示停頓——MiniMax TTS 會把它唸成明顯拖長的停頓，
  搭配 A2 CEFR 的慢速再疊加會變得過慢。要停頓改用句號/逗號斷句，或用 em dash `—`。
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


def _series_block(series_context: tuple[str, ...], parent_title: str | None = None) -> str:
    """頻道系列感——跟 avoid_facts 職責相反：那邊「不要重複」，這裡「可以呼應」。

    series_context 是該頻道最近 2-3 集的標題，純粹提供自然呼應的素材，建立
    「這是同一個頻道」的連續感；不是硬性規則，LLM 不呼應也完全合法，因此措辭
    只用「若自然，可簡短呼應」，不像 avoid_facts 那樣是程式會擋下來的硬性規則。

    parent_title：保留給未來頻道顯示名稱使用（目前 PodState 沒有對應欄位，
    呼叫端一律不帶，None 時該行退化成無頻道名稱版本）。

    series_context 為空 → 回傳空字串，prompt 不會多出一個空標題的區塊
    （呼叫端沿用 _sources_block/_verified_research_block 同款的空字串合併寫法）。
    """
    if not series_context:
        return ""
    lines = ["\n# SERIES CONTEXT（僅供自然呼應，非必要）"]
    recent = "、".join(series_context)
    if parent_title:
        lines.append(f"本頻道《{parent_title}》最近幾集談過：{recent}。")
    else:
        lines.append(f"本頻道前幾集談過：{recent}。")
    lines.append("若自然，可簡短呼應建立連續感；但不要重述其內容。")
    return "\n".join(lines)


def _verified_research_block(
    verified_claims: list[VerifiedClaim], source_conflicts: list[str]
) -> str:
    """只把可採用主張列為依據，並保留尚未解決的來源衝突。"""
    usable = [claim for claim in verified_claims if claim.usable]
    if not usable and not source_conflicts:
        return ""

    lines = ["# VERIFIED CLAIMS（只有本區主張可視為已交叉驗證）"]
    if usable:
        lines.extend(
            (
                f"- {claim.claim} "
                f"[sources: {', '.join(claim.supporting_source_ids)}; "
                f"confidence: {claim.confidence:.2f}]"
            )
            for claim in usable
        )
    else:
        lines.append("- (none)")

    if source_conflicts:
        lines.append("\n# SOURCE CONFLICTS（不可自行選邊或寫成確定事實）")
        lines.extend(f"- {conflict}" for conflict in source_conflicts)
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
    verified_claims: list[VerifiedClaim] | None = None,
    source_conflicts: list[str] | None = None,
    feedback: list[str] | None = None,
    series_context: tuple[str, ...] = (),
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
        f"{_verified_research_block(verified_claims or [], source_conflicts or [])}\n\n"
        f"{_series_block(series_context)}\n\n"
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
    series_context: tuple[str, ...] = (),
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
        + "# ENGLISH QUALITY（text 會直接進 TTS 唸出來，錯了聽眾聽得到）\n"
        "- Proofread every `text` line: correct, natural native-speaker grammar "
        '(e.g. "headquartered in Atlanta", never "headquarter in Atlanta").\n'
        "- Spell out numbers the way a native speaker SAYS them: 38,900 is "
        '"thirty-eight thousand nine hundred", never "thirty eight nine hundred".\n'
        "- Use natural spoken contractions consistently (it's / that's / let's / "
        'don\'t), never stiff uncontracted forms like "let us" or "that is not" '
        "in casual dialogue — keep the SAME register across every segment.\n\n"
        "# BILINGUAL\n"
        "- Every line MUST have `zh` in natural Taiwan Mandarin (台灣正體中文), "
        "translate the meaning naturally, NOT word-for-word.\n"
        "- `zh` 絕對不能出現簡體字（例如「两」「国」「时」寫成簡體），一律用台灣正體字，"
        "這是程式會擋下來的硬性規則。\n"
        "- `zh` 只能翻譯「這一行自己的」`text`，禁止把下一行的內容提前挪進這一行的 zh，"
        "也禁止兩個連續行的 zh 一模一樣（這是程式會擋下來的硬性規則）。\n\n"
        "# SOURCES\n"
        f"{_sources_block(sources or [], avoid_facts)}\n\n"
        f"{_series_block(series_context)}\n\n"
        f"{few_shots}\n\n"
        f"{_EMOTION_GUIDE}\n"
        "JSON SCHEMA (must match exactly, ONLY the script array):\n"
        '{"script": [{"speaker": ' + schema_speaker + ', "text": str, "zh": str, '
        '"pause_before": bool, "emotion": "happy"|"sad"|"angry"|"fearful"|"disgusted"'
        '|"surprised"|"neutral"}]}\n'
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
    # [opt-p2] system 區塊掛 cache_control:ephemeral。注意 MiniMax M3 的 anthropic
    # 相容端點吃下這個欄位但不做事（實測 cache_creation_input_tokens 恆為 0），真正
    # 會命中的是它的被動 prefix cache——詳見 _generate_segment 的 history 說明。
    # 標記留著是為了換供應商/升級時零改動,對 MiniMax 無副作用。
    out: list[Any] = []
    for m in msgs:
        if m["role"] == "system":
            out.append(
                SystemMessage(
                    content=[
                        {
                            "type": "text",
                            "text": m["content"],
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                )
            )
        elif m["role"] == "user":
            out.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            out.append(AIMessage(content=m["content"]))
    return out


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
        "cover_icon": outline.cover_icon,
        "extracted_facts": [f.model_dump() for f in outline.extracted_facts],
        "target_vocab": [v.model_dump() for v in outline.target_vocab],
        "script": [ln.model_dump() for ln in merged_lines],
        "format": fmt,
    }
    return ScriptJSON.model_validate(payload)


async def _generate_outline(
    chat: Any,
    state: PodState,
    *,
    usage_node: str,
    cefr: str,
    length_tier: str,
    feedback: list[str] | None = None,
    collector: MetricsCollector | None = None,
) -> tuple[ScriptOutline, dict[str, Any], dict[str, int]]:
    """打 LLM 產大綱。回傳 (outline, usage_metadata_by_call, total_usage)。

    失敗重試 _MAX_OUTLINE_RETRIES 次（純 parse fix），耗盡 raise GenerationError
    給外層 RetryPolicy 接手。RateLimitError 改回傳特殊 sentinel 給 caller 路由。
    """
    build_msgs = functools.partial(
        _build_outline_messages,
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
        verified_claims=state.get("verified_claims"),
        source_conflicts=state.get("source_conflicts"),
        series_context=tuple(state.get("series_context") or ()),
    )
    msgs = build_msgs(feedback=feedback)

    last_exc: GenerationError | None = None
    total_usage = {"input_tokens": 0, "output_tokens": 0}
    for attempt in range(_MAX_OUTLINE_RETRIES + 1):
        call_start = time.monotonic()
        try:
            ai_msg = await chat.ainvoke(_to_lc_messages(msgs))
        except RateLimitError:
            logger.warning("%s 撞限流 big_topic=%s (outline)", usage_node, state["big_topic"])
            raise

        usage = _record_llm_usage(
            collector,
            ai_msg,
            node=usage_node,
            call="outline",
            call_start=call_start,
            attempt=attempt + 1,
        )
        total_usage["input_tokens"] += usage["input_tokens"]
        total_usage["output_tokens"] += usage["output_tokens"]

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
                msgs = build_msgs(feedback=[f"上一版大綱無法解析成合法結構：{exc}"])
                continue
    assert last_exc is not None
    raise last_exc


async def _generate_segment(  # type: ignore[return]
    chat: Any,
    state: PodState,
    *,
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
    history: list[dict[str, str]] | None = None,
    collector: MetricsCollector | None = None,
) -> tuple[list[ScriptLine], dict[str, int], list[dict[str, str]]]:
    """打 LLM 寫單段對話。回傳 (script_lines, total_usage, 接下一段用的 history)。

    Level 1 段落內重試：每段生成完立刻做《段內 vocab 命中 + 段內 zh 不重複》
    兩項檢查，沒過帶著具體錯誤內容重打這一段，上限 _MAX_SEGMENT_RETRIES 次。
    RateLimitError 讓 _invoke_writer 整段路由（不 raise 自身）。

    history 非空 = 對話式接龍：system 沿用第一段那份，這段只接上新的 user turn。
    MiniMax 的被動 prefix cache 是以「整個曾經送出去的 request」為條目，新請求要
    命中必須有一次過去送過的完整請求剛好是它的前綴——所以 [sys,u1] 會命中
    [sys,u1,a1,u2]，但同一份 sys 換掉 user 的 [sys,u2] 不會命中（實測 2026-08-04：
    接龍第 2 通起命中 83-88%，全額計費 input -60%）。重試時取代最後一個 user turn，
    前面所有輪次仍是合法前綴、照樣命中，壞掉的那版輸出不進 history（免得模型照抄）。
    """
    fmt = state.get("format", "dialogue")
    build_msgs = functools.partial(
        _build_segment_messages,
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
        series_context=tuple(state.get("series_context") or ()),
    )

    def compose(fb: list[str] | None) -> list[dict[str, str]]:
        built = build_msgs(feedback=fb)
        return [*history, built[1]] if history else built

    msgs = compose(feedback)

    total_usage = {"input_tokens": 0, "output_tokens": 0}
    last_exc: GenerationError | None = None
    for attempt in range(_MAX_SEGMENT_RETRIES + 1):
        call_start = time.monotonic()
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

        usage = _record_llm_usage(
            collector,
            ai_msg,
            node=usage_node,
            call="segment",
            call_start=call_start,
            attempt=attempt + 1,
            segment_index=segment_index,
        )
        total_usage["input_tokens"] += usage["input_tokens"]
        total_usage["output_tokens"] += usage["output_tokens"]

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
                msgs = compose([f"上一版這段 JSON 解析失敗：{exc}"])
                continue
            raise

        # Level 1 段落內檢查：vocab 命中 + 段內 zh 不重複。
        seg_text = " ".join(ln.text for ln in lines)
        missing_vocab = missing_vocab_words(seg_text, segment_vocab)
        dup_idx = first_duplicate_adjacent_index([ln.zh for ln in lines])
        if not missing_vocab and dup_idx is None:
            return lines, total_usage, [*msgs, {"role": "assistant", "content": ai_msg.content}]

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
            msgs = compose(feedback_msgs)
            continue
        # mypy 不追蹤「for 跑完 last_exc 必非 None」這個 invariant——assert 在 strict
        # 模式不會 narrow，raise last_exc 在 mypy 看來仍可能為 None。
        assert last_exc is not None
        raise last_exc


# ── 短句正規化：純 Python 機械切分，內容一字不改 ────────────────
#
# 中英對照要對得上，每行英文就不能太長。而「切分」只是決定在哪裡斷，內容一字不改，
# 所以不需要 LLM：英文照詞界切（切點優先落在標點結尾的詞之後），中譯照各段英文詞數
# 的比例切、再吸附到最近的中文標點。兩邊段數一致、只做字串切片，串回去必然等於原文，
# 保真由切法本身保證，不需要事後逐字驗證，也沒有「驗不過就整條退回」這回事。
#
# 舊版把切分丟回 LLM 再驗證，實測每集燒 ~43k tokens／260 秒卻幾乎沒效果：規則同時要求
# 「切 2-3 段」與「每段 ≤12 詞」，兩者相乘代表一行超過 36 詞在規則上就不可能通過驗證，
# 而實測腳本裡 40-80 詞的行很常見；這些行每輪被送去切、每輪被退回沿用原行，跑滿三層
# 遞迴後仍有 24-36 行超長。機械切分沒有段數上限，一次就切乾淨。

_TARGET_TEXT_WORD_COUNT = 12
# A2 等級學習者能消化的英文短句上限（12 詞）：Oxford A2 wordlist 平均句長
# 8-12 詞；每段 >12 詞的中英對照翻頁就難對齊單字。改字而非改 tokens：以
# words 計，LLM 看得到目標、人類驗收看得到指標，跟 LLM-side 的 token metric 解耦。
# 用法：len(line.text.split()) > _TARGET_TEXT_WORD_COUNT 判定是否需切。

# 切點優先落在這些收尾符號之後（句意較完整）；找不到就照詞數硬切。詞尾可能還帶
# 引號／括號（例：`words,"`），所以標點後允許再跟一個收尾符號。
_EN_BREAK_RE = re.compile(r"[.!?,;:—–][\"'’”）)\]]?$")
_ZH_BREAK_CHARS = "，。！？、；：—…）」』"
# 在理想切點附近找標點的容忍範圍（英文以詞計、中文以字計）。放太寬會讓段落長度失衡，
# 放太窄則幾乎都退回硬切；3/4 是「一個短子句」的量級。
_EN_SNAP_WINDOW = 3
_ZH_SNAP_WINDOW = 4


def _split_en_words(tokens: list[str], n_parts: int) -> list[list[str]]:
    """把 tokens 切成 n_parts 段，切點優先落在標點結尾的詞之後。

    每輪用「剩餘詞數 / 剩餘段數」無條件進位當本段上限 `even`：只要初始
    n_parts >= ceil(總詞數 / 12)，這個上限就恆 <= 12，因此每一段都保證不超標
    （往前吸附標點只會讓本段更短，而 `lo` 保證剩下的詞仍塞得進剩餘段數）。
    """
    parts: list[list[str]] = []
    rest = tokens
    for remaining in range(n_parts, 1, -1):
        even = -(-len(rest) // remaining)
        # 下界：切太少會讓後面的段吃不下（每段最多 even 詞）
        lo = max(1, len(rest) - (remaining - 1) * even)
        cut = even
        for offset in range(1, _EN_SNAP_WINDOW + 1):
            cand = even - offset
            if cand < lo:
                break
            if _EN_BREAK_RE.search(rest[cand - 1]):
                cut = cand
                break
        parts.append(rest[:cut])
        rest = rest[cut:]
    parts.append(rest)
    return parts


def _splits_latin_word(zh: str, pos: int) -> bool:
    """pos 這個切點是否卡在一個拉丁單字/數字中間（例：Hugging Face 被切成 H|ugging）。"""
    if not 0 < pos < len(zh):
        return False
    before, after = zh[pos - 1], zh[pos]
    return before.isascii() and before.isalnum() and after.isascii() and after.isalnum()


def _avoid_latin_word_cut(zh: str, pos: int, lo: int, hi: int) -> int:
    """把卡在拉丁單字中間的切點挪到最近的邊界；範圍內都挪不開就原樣回傳。

    中譯常內嵌英文專有名詞（Hugging Face、GPT-5.5），按字元比例切很容易切在單字中間，
    顯示出來會很難讀。這是純位置調整，不影響「切片串回去等於原字串」的保證。
    """
    if not _splits_latin_word(zh, pos):
        return pos
    candidates: list[int] = [*range(pos + 1, hi + 1), *range(pos - 1, lo - 1, -1)]
    for cand in candidates:
        if not _splits_latin_word(zh, cand):
            return cand
    return pos


def _split_zh_text(zh: str, weights: list[int]) -> list[str] | None:
    """照 weights（各段英文詞數）比例把 zh 切成同段數，切點吸附到最近的中文標點。

    純切片，串回去逐字等於原字串。zh 短到切不出「每段至少 1 字」時回 None，
    由呼叫端決定不切這行——ScriptLine.zh 有 min_length=1，寧可留長行也不能生空 zh。
    """
    n = len(weights)
    if len(zh) < n:
        return None
    total_w = sum(weights)
    cuts: list[int] = []
    used = 0
    for i in range(n - 1):
        ideal = round(len(zh) * sum(weights[: i + 1]) / total_w)
        lo, hi = used + 1, len(zh) - (n - 1 - i)
        pos = min(max(ideal, lo), hi)
        for offset in range(_ZH_SNAP_WINDOW + 1):
            snapped = next(
                (
                    c
                    for c in (pos + offset, pos - offset)
                    if lo <= c <= hi and zh[c - 1] in _ZH_BREAK_CHARS
                ),
                None,
            )
            if snapped is not None:
                pos = snapped
                break
        pos = _avoid_latin_word_cut(zh, pos, lo, hi)
        cuts.append(pos)
        used = pos
    bounds = [0, *cuts, len(zh)]
    return [zh[a:b] for a, b in pairwise(bounds)]


def _split_line(line: ScriptLine, target_max_words: int) -> list[ScriptLine]:
    """單行切分；不需要切（或 zh 短到切不動）時回傳只含原行的 list。"""
    tokens = line.text.split()
    if len(tokens) <= target_max_words:
        return [line]

    n_parts = min(-(-len(tokens) // target_max_words), len(tokens))
    en_parts = _split_en_words(tokens, n_parts)
    zh_parts = _split_zh_text(line.zh, [len(p) for p in en_parts])
    if zh_parts is None:
        return [line]

    return [
        line.model_copy(
            update={
                "text": " ".join(en),
                "zh": zh,
                # 只有第一段承接原行的 pause_before（章節邊界資訊），
                # 其餘段是同一句話的延續，不該再插停頓。
                "pause_before": line.pause_before if i == 0 else False,
            }
        )
        for i, (en, zh) in enumerate(zip(en_parts, zh_parts, strict=True))
    ]


def _normalize_line_lengths(
    script: ScriptJSON,
    *,
    target_max_words: int = _TARGET_TEXT_WORD_COUNT,
) -> ScriptJSON:
    """把 script.script 裡 text 超過 target_max_words 的行切短。

    - 全部都短 → 原物件直接回傳（零成本 no-op）
    - 切完重組 ScriptJSON 驗證失敗（例如切出相鄰重複 zh）→ atomic fallback 回原
      script：行長是輔助品質目標，不該讓整輪寫稿被判失敗重跑。
    """
    lines: list[ScriptLine] = []
    changed = False
    for line in script.script:
        parts = _split_line(line, target_max_words)
        changed = changed or len(parts) > 1
        lines.extend(parts)
    if not changed:
        return script

    payload = script.model_dump()
    payload["script"] = [line.model_dump() for line in lines]
    try:
        return ScriptJSON.model_validate(payload)
    except PydanticValidationError as exc:
        logger.warning("行長切分後 ScriptJSON 驗證失敗，沿用未切分腳本：%s", exc)
        return script


async def _invoke_writer(
    chat: Any,
    state: PodState,
    settings: Settings,
    *,
    engine_label: str,
    usage_node: str,
    collector: MetricsCollector | None = None,
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
    # A/B 開關：state 沒帶就走 settings 預設（見 shared/config.py:writer_conversation_mode）
    conversation_mode = state.get("writer_conversation", settings.writer_conversation_mode)
    if collector is not None:
        # 寫進 metrics 才能事後純用 DB 分組比較，不必回頭對照當初 enqueue 了什麼
        collector.set_research_summary(writer_conversation=conversation_mode)
    word_floor = _word_floor(cefr, length_tier)

    best_result: ScriptJSON | None = None
    best_word_count = -1
    # best_result 的段落內容要跟著一起記——單獨用最後一輪的 segment_scripts 會跟
    # best_result 實際對應的那一輪錯位（見下面兩處 fallback return）。
    best_segment_scripts: list[list[ScriptLine]] | None = None

    # 第一階段：生大綱。
    # [opt-p1] rewrite pass 時若上一輪 outline 還在 state、且 feedback 沒要求改大綱,
    # 跳過重打直接重用——大綱規劃通常沒問題,rewrite 多半是段落內容不佳,重打 outline
    # 純屬浪費 ~1.3K input + 25s。escape hatch: feedback 包含 "#OUTLINE" 字樣時
    # 仍重打(LLM 明確指出大綱本身有問題,罕見但可能發生)。
    outline: ScriptOutline | None = None
    outline_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
    reuse_outline = (
        state.get("rewrite_iterations", 0) > 0
        and state.get("outline") is not None
        and not any("#OUTLINE" in fb.upper() for fb in base_feedback)
    )
    if reuse_outline:
        outline = state["outline"]
        logger.info(
            "%s rewrite 第 %d 輪重用上一輪 outline big_topic=%s, 跳過重打",
            usage_node,
            state.get("rewrite_iterations", 0),
            state["big_topic"],
        )
    else:
        try:
            outline, _, outline_usage = await _generate_outline(
                chat,
                state,
                usage_node=usage_node,
                cefr=cefr,
                length_tier=length_tier,
                feedback=base_feedback or None,
                collector=collector,
            )
        except RateLimitError:
            # outline 還沒生成成功;若 state 已有上一輪 outline,保留它給後續 retry
            existing_outline = state.get("outline")
            return {
                "rate_limited": True,
                "engine_used": engine_label,
                **({"outline": existing_outline} if existing_outline is not None else {}),
            }
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
        targets = [(max(1, int(w * adjuster)), boundary) for (w, boundary) in base_segment_words]
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
        # 對話式接龍的累積訊息（None = 每段各自獨立呼叫,現行行為）。每輪重打都從頭
        # 累積：整輪重打時第一通與上一輪逐字相同,一樣吃得到快取。
        history: list[dict[str, str]] | None = None
        # [opt-p3] partial_rewrite: 只重生 affected_segments 指定的段,其他段沿用上一輪
        prev_segs_reuse = state.get("previous_segment_scripts") or []
        target_segs = state.get("affected_segments") or []
        is_partial = (
            state.get("rewrite_iterations", 0) > 0
            and bool(target_segs)
            and len(prev_segs_reuse) == len(outline.segments)
        )
        if is_partial:
            logger.info(
                "%s partial_rewrite 第 %d 輪 big_topic=%s: 只重打段 %s,其餘沿用",
                usage_node,
                state.get("rewrite_iterations", 0),
                state["big_topic"],
                target_segs,
            )
        try:
            for seg_idx, (outline_seg, (target_words, boundary)) in enumerate(
                zip(outline.segments, targets, strict=True)
            ):
                # [opt-p3] 非失敗段直接抄上一輪
                if is_partial and seg_idx not in target_segs:
                    segment_scripts.append(prev_segs_reuse[seg_idx])
                    continue
                prev_tail = segment_scripts[-1][-3:] if segment_scripts else []
                lines, seg_usage, next_history = await _generate_segment(
                    chat,
                    state,
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
                    history=history,
                    collector=collector,
                )
                total_usage["input_tokens"] += seg_usage["input_tokens"]
                total_usage["output_tokens"] += seg_usage["output_tokens"]
                segment_scripts.append(lines)
                # partial_rewrite 時不接龍：被跳過的段沒有 assistant turn,鏈是斷的,
                # 硬接只會讓前綴對不上白白多送 token。那條路本來就是重打單段,
                # 而重打的請求與上一輪逐字相同、自己就會命中快取。
                if conversation_mode and not is_partial:
                    history = next_history
        except RateLimitError:
            # outline 已生成,後續 retry 可直接跳過重打
            return {
                "rate_limited": True,
                "engine_used": engine_label,
                "outline": outline,
            }
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
            # 短句正規化：純 Python 機械切分，不打 LLM、不影響主流程。
            # 因為覆蓋了所有初次生成與 rewrite（含 partial_rewrite）路徑，
            # 後續 judge 永遠看得到已切分版本，不需改 router / state。
            full_script = _normalize_line_lengths(full_script)
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
            best_segment_scripts = segment_scripts

        if word_count >= word_floor:
            return {
                "script": full_script,
                "outline": outline,
                "previous_segment_scripts": segment_scripts,
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
                "outline": outline,
                "previous_segment_scripts": best_segment_scripts,
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
        last_exc = GenerationError(f"整集 {word_count} 字低於 {word_floor} 下限")

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
        "outline": outline,
        "previous_segment_scripts": best_segment_scripts,
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
        collector=_collector(config),
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
        collector=_collector(config),
    )
    if out.get("rate_limited"):
        out["errors"] = ["failover engine also rate-limited"]
    return out


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
