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

import functools
import json
import logging
import re
import shutil
import time
import uuid
from typing import Any, Literal, TypedDict, cast

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END
from psycopg.errors import ForeignKeyViolation
from pydantic import BaseModel, Field
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
from shared.db import repo as app_repo
from shared.errors import GenerationError, RateLimitError
from shared.idempotency import compute_idempotency_key
from shared.models import (
    ClaimCheck,
    ClaimVerification,
    Cue,
    EvidenceCard,
    JudgeVerdict,
    ResearchQuestion,
    ScriptFormat,
    ScriptJSON,
    ScriptLine,
    ScriptOutline,
    SourcedFact,
    SourceSnippet,
    VerifiedClaim,
)
from shared.push import notify_user
from shared.script_contract import first_duplicate_adjacent_index, missing_vocab_words

from .metrics import MetricsCollector
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


def _collector(config: RunnableConfig) -> MetricsCollector | None:
    """metrics collector 未接（測試直接 graph.ainvoke 沒帶 configurable）時回 None。"""
    return cast(MetricsCollector | None, _ctx(config).get("metrics_collector"))


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
    # [opt-p2] system 區塊掛 cache_control:ephemeral — 跨集跨呼叫命中時
    # MiniMax / Anthropic 只算 cache_read(輸入計費 10%)；不通就 graceful 退化成
    # 一般 text block,零影響(詳見 chat.py:_lc_to_anthropic)。
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
    return out


def _usage_from_ai_msg(ai_msg: Any) -> dict[str, int]:
    """從 chat.py 塞進 AIMessage.usage_metadata 的量抽出來；缺欄位時回 0。"""
    meta = getattr(ai_msg, "usage_metadata", None) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
        "cache_creation_tokens": int(meta.get("cache_creation_input_tokens", 0)),
        "cache_read_tokens": int(meta.get("cache_read_input_tokens", 0)),
    }


def _record_llm_usage(
    collector: MetricsCollector | None,
    ai_msg: Any,
    *,
    node: str,
    call: str,
    call_start: float,
    attempt: int = 1,
    segment_index: int | None = None,
) -> dict[str, int]:
    """讀 ai_msg.usage_metadata，寫一筆 collector.record_llm_call；回傳 usage 供呼叫端累加。

    每個 chat.ainvoke() 呼叫點都該接這一行，取代手動組 record_llm_call 的樣板——
    漏貼樣板就是 cost 對某個節點隱形的根因（_normalize_line_lengths 曾經漏記，
    _generate_segment 曾經漏記 cache 欄位，都是同一種手動複製貼上失誤）。
    """
    usage = _usage_from_ai_msg(ai_msg)
    if collector is not None:
        collector.record_llm_call(
            node=node,
            call=call,
            attempt=attempt,
            duration_ms=int((time.monotonic() - call_start) * 1000),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            cache_creation_tokens=usage["cache_creation_tokens"],
            cache_read_tokens=usage["cache_read_tokens"],
            segment_index=segment_index,
        )
    return usage


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
    collector: MetricsCollector | None = None,
) -> tuple[list[ScriptLine], dict[str, int]]:
    """打 LLM 寫單段對話。回傳 (script_lines, total_usage_for_this_segment)。

    Level 1 段落內重試：每段生成完立刻做《段內 vocab 命中 + 段內 zh 不重複》
    兩項檢查，沒過帶著具體錯誤內容重打這一段，上限 _MAX_SEGMENT_RETRIES 次。
    RateLimitError 讓 _invoke_writer 整段路由（不 raise 自身）。
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
    msgs = build_msgs(feedback=feedback)

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
                msgs = build_msgs(feedback=[f"上一版這段 JSON 解析失敗：{exc}"])
                continue
            raise

        # Level 1 段落內檢查：vocab 命中 + 段內 zh 不重複。
        seg_text = " ".join(ln.text for ln in lines)
        missing_vocab = missing_vocab_words(seg_text, segment_vocab)
        dup_idx = first_duplicate_adjacent_index([ln.zh for ln in lines])
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
            msgs = build_msgs(feedback=feedback_msgs)
            continue
        # mypy 不追蹤「for 跑完 last_exc 必非 None」這個 invariant——assert 在 strict
        # 模式不會 narrow，raise last_exc 在 mypy 看來仍可能為 None。
        assert last_exc is not None
        raise last_exc


# ── 短句正規化：把超長 zh 丟回 LLM 切分 2-3 行，不改內容 ────────
#
# LLM 寫稿對「每行 zh <= N 字」只有 soft prompt 約束；實測同 prompt 連跑兩次
# avg 15.4 vs 23.9 字。在 _invoke_writer 每輪 _merge_outline_and_segments 之
# 後跑這個 pass 把超長行回丟 LLM 切分，deterministic 驗證「內容字元級一致」
# 後再回傳。LLM 只負責找切割邊界，所有保真檢查 deterministic 走 code。
#
# 不改 ScriptLine.zh schema（保留 min_length=1）：LLM/網路失敗時 atomic
# fallback 把原超長腳本交回去，pod 流程不會因輔助切分失敗中止。max_length
# 寫死會把 fallback 路徑封死，這裡刻意不寫。


_TARGET_TEXT_WORD_COUNT = 12
# A2 等級學習者能消化的英文短句上限（12 詞）：Oxford A2 wordlist 平均句長
# 8-12 詞；每段 >12 詞的中英對照翻頁就難對齊單字。改字而非改 tokens：以
# words 計，LLM 看得到目標、人類驗收看得到指標，跟 LLM-side 的 token metric 解耦。
# 用法：len(line.text.split()) > _TARGET_TEXT_WORD_COUNT 判定是否需切。


class _LineSplitItem(BaseModel):
    """LLM 對單一超長行的切分結果。source_index 對齊 script.script 絕對位置，
    不用隱式順序配對，避免多行 batch 對位錯誤。

    不在 schema 層卡 lines 長度：由 _validate_split_item 在 post-parse 階段
    拒絕單項並 fallback 該行——這樣 bad item 不會讓整個 batch 在解析時被丟掉。
    """

    source_index: int = Field(ge=0)
    lines: list[ScriptLine]


class _LineSplitBatch(BaseModel):
    """LLM 一次回傳所有超長行的切分結果。schema 寬鬆，items 至少 1 條。"""

    items: list[_LineSplitItem] = Field(min_length=1)


_LINE_SPLIT_SYSTEM = """\
你是 podcast 英文短句切分器，唯一的任務是把每條超長英文行切成 2 或 3 行短句。

# 背景
學習者在對照中英學習新單字。每行 text 太長時，聽到一段英文沒對到中譯的單字就過去，
回頭對單字就很困難。所以要把長英文句切短，讓每段的英文單字數 <= target_max_words，
這樣單字 ↔ 中譯逐段對得上。

# 嚴格規則（不遵守 = 整批退回）
- 對 input.items 裡的「每一個」條目，都必須在輸出 items 中給一個對應項目。
  不可跳過任何條目，也不可輸出 input.items 沒有的 source_index。
- 每個條目切 2 或 3 行；不可 1 行（= 沒切）也不可 4 行以上。
- 每個 part 的 speaker 必須與輸入條目的 speaker「完全相同」。
- 拼接 parts.text 必須 = 輸入條目 text（單字級一致：詞序不變、不增詞、不減詞、不換詞）。
  用空白斷詞後逐詞比對；標點可微調，但每個英文詞必須完整保留、不能改寫。
- 拼接 parts.zh 必須 = 輸入條目 zh（中文字元級一致：順序不變、不增字、不減字、不換字）。
  中譯跟英文切點一致切：英文第 1 段對應 zh 第 1 段，英文第 2 段對應 zh 第 2 段。
- 不要改寫措辭、不要重新翻譯、不要調換詞序、不要同義替換。
- 每個 part 的 text 單字數（以空白切開計）必須 <= target_max_words。
- 中文常見「定語後置」結構（修飾語在前、被修飾的詞在後，例如「在上一代完全沒有的功能」
  裡「功能」被前面一整串修飾語包住）：切這種句子時，直接在原字串裡找一個逗號或自然停頓
  機械地切開，即使切出來的某一段單獨看語法不完整也沒關係。絕對不要為了讓每段語法完整，
  就把被修飾的詞（例句中的「功能」）搬到前一段或同時留在兩段——原字串裡每個字只能出現
  一次，出現的位置也不能變。

# 反例（錯誤示範，不要這樣做）
輸入 zh："這家公司昨天發表了一款新產品，它有好幾項在上一代完全沒有的功能。"
錯誤輸出（把「功能」搬到前面湊語法完整，等於重寫）：
  第 1 段："它有好幾項沒有的功能"　第 2 段："在上一代完全沒有"　← 錯：字被搬動又重複
正確輸出（照原字串順序機械切開，就算第 2 段單獨看不像完整句子）：
  第 1 段："這家公司昨天發表了一款新產品，"
  第 2 段："它有好幾項在上一代完全沒有的功能。"

# 範例（target_max_words = 12）
輸入：{"source_index": 0, "speaker": "Sarah",
       "text": ("Quantum physics describes nature at the smallest scales "
                "and powers much of modern technology."),
       "zh": "量子物理描述最小尺度的自然，也支撐了許多現代科技。"}
正確輸出之一（切成 2 行，每段 ≤12 詞）：
{"source_index": 0, "lines": [
  {"speaker": "Sarah",
   "text": "Quantum physics describes nature at the smallest scales.",
   "zh": "量子物理描述最小尺度的自然。"},
  {"speaker": "Sarah",
   "text": "It also powers much of modern technology.",
   "zh": "它也支撐了許多現代科技。"}
]}

# JSON schema
{"items": [{"source_index": int, "lines": [{"speaker": str, "text": str, "zh": str}]}]}
"""


# ── 標點容忍比對 ──
# LLM 切分常常會調整標點位置（例：「地板，莎拉」拆成「地板莎拉」連寫），這個動作不算
# 改內容，應該放行；嚴格字元級比對會誤殺。規則：去掉所有標點與空白後，剩餘 char 序列
# 必須逐字相等。Latin 字 / 數字 / CJK ideograph 保留；標點、空格、換行丟掉。
_PUNCT_PATTERN = re.compile(
    r"[\s，。！？、；：「」（）()\"'`…—–_\-\\/|,.;:!?]+"
)


def _content_chars(text: str) -> str:
    return _PUNCT_PATTERN.sub("", text)


def _validate_split_item(
    item: _LineSplitItem,
    original: ScriptLine,
    *,
    target_max_words: int,
) -> list[ScriptLine]:
    """驗證單一 split item，回傳 metadata 重建後的 parts list。失敗 raise ValueError。"""
    if not (2 <= len(item.lines) <= 3):
        raise ValueError(f"切分行數 {len(item.lines)} 不在 2-3 範圍")

    rebuilt: list[ScriptLine] = []
    for i, part in enumerate(item.lines):
        if part.speaker != original.speaker:
            raise ValueError(
                f"第 {i} 段 speaker={part.speaker!r} 與原行 {original.speaker!r} 不符"
            )
        rebuilt.append(
            part.model_copy(
                update={
                    # metadata 強制覆寫，避免 LLM 亂設；語意只有第一段承接原本的
                    # pause_before/chapter 邊界資訊，其餘段統一 False。
                    "pause_before": original.pause_before if i == 0 else False,
                    "emotion": original.emotion,
                }
            )
        )

    joined_zh_chars = _content_chars("".join(p.zh for p in rebuilt))
    original_zh_chars = _content_chars(original.zh)
    if joined_zh_chars != original_zh_chars:
        raise ValueError(
            f"split 後 zh 內容與原行不一致：{joined_zh_chars!r} vs {original_zh_chars!r}"
        )
    joined_text_chars = _content_chars("".join(p.text for p in rebuilt))
    original_text_chars = _content_chars(original.text)
    if joined_text_chars != original_text_chars:
        raise ValueError("split 後 text 內容與原行不一致")

    for i, p in enumerate(rebuilt):
        if len(p.text.split()) > target_max_words:
            raise ValueError(
                f"第 {i} 段 text 單字數 {len(p.text.split())} 超過 {target_max_words}"
            )

    return rebuilt


def _validate_split_batch(
    originals: list[tuple[int, ScriptLine]],
    batch: _LineSplitBatch,
    *,
    target_max_words: int,
) -> dict[int, list[ScriptLine]]:
    """保真檢查 batch.items，per-item 失敗僅丟該行（不改別行）。

    Batch-level（數量、重複 index）失敗 raise → 外層 atomic fallback 回原 ScriptJSON。
    Item-level（speaker 不符、拼接不一致、長度超限）失敗 log + skip → 該行沿用原行，
    其他有效 item 仍套用。

    回傳 {source_index: [rebuilt_parts_with_overridden_metadata]}，
    給 _normalize_line_lengths 重新組裝用。
    """
    if not batch.items:
        raise ValueError("split batch 為空")

    seen_index: set[int] = set()
    for item in batch.items:
        if item.source_index in seen_index:
            raise ValueError(f"split batch 重複 source_index={item.source_index}")
        seen_index.add(item.source_index)

    by_index = {item.source_index: item for item in batch.items}

    replacements: dict[int, list[ScriptLine]] = {}
    for source_index, original in originals:
        split_item: _LineSplitItem | None = by_index.get(source_index)
        if split_item is None:
            # 該行沒給 split → 沿用原行（不算失敗）
            continue
        try:
            replacements[source_index] = _validate_split_item(
                split_item, original, target_max_words=target_max_words
            )
        except ValueError as exc:
            logger.warning(
                "source_index=%d split 驗證失敗，沿用原行：%s",
                source_index,
                exc,
            )
            continue

    return replacements


_MAX_NORMALIZE_DEPTH = 3


async def _normalize_line_lengths(
    script: ScriptJSON,
    chat: Any,
    *,
    target_max_words: int = _TARGET_TEXT_WORD_COUNT,
    usage_node: str = "writer",
    collector: MetricsCollector | None = None,
    _depth: int = 0,
) -> ScriptJSON:
    """把 script.script 裡 text 單字數 > target_max_words 的行回丟 LLM 切分。

    學英文 podcast 每段太長就對不到單字 ↔ 中譯，所以切英文；中譯跟著英文切點切。

    - 全短 → 零成本 no-op（return 原 script）
    - 有長行 → 一次 batch call；不逐行 API
    - LLM 失敗 / 解析失敗 / 任一項保真檢查失敗 → 該行沿用原行（per-item fallback），
      其他有效 split 照套用
    - 仍有部分行超長 → 遞迴叫 _normalize_line_lengths（最多 _MAX_NORMALIZE_DEPTH 層）
    - 重組 ScriptJSON 失敗 → atomic fallback 回原 script（pod 不會因輔助切分失敗中止）
    """
    # 測試模式短路：FakeChatModel 預設自動 no-op，避免污染測試 LLM call 計數
    # （測試 fixture text 行被灌到 100+ 詞過 word_floor，若每輪都真打 normalize 會
    # 多耗 LLM call slot）。想真的驗 normalize 走完整 split 路徑：
    # chat = FakeChatModel(skip_normalize_noop=True, ...)。
    if getattr(chat, "is_fake", False) and not getattr(chat, "skip_normalize_noop", False):
        long_items_count = sum(
            1 for ln in script.script if len(ln.text.split()) > target_max_words
        )
        if long_items_count:
            logger.info(
                "_normalize_line_lengths fake-mode noop（%d 行超長）— "
                "未呼叫 LLM；想真跑可設 chat.skip_normalize_noop=True",
                long_items_count,
            )
        return script

    long_items = [
        (idx, line)
        for idx, line in enumerate(script.script)
        if len(line.text.split()) > target_max_words
    ]
    if not long_items:
        return script
    if _depth >= _MAX_NORMALIZE_DEPTH:
        return script  # 避免無限遞迴：超過深度直接回原 script

    prompt_payload = {
        "target_max_words": target_max_words,
        "items": [
            {
                "source_index": source_index,
                "speaker": line.speaker,
                "text": line.text,
                "zh": line.zh,
                "pause_before": line.pause_before,
                "emotion": line.emotion,
            }
            for source_index, line in long_items
        ],
    }
    msgs = [
        {"role": "system", "content": _LINE_SPLIT_SYSTEM},
        {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
    ]

    call_start = time.monotonic()
    try:
        ai_msg = await chat.ainvoke(_to_lc_messages(msgs))
    except Exception as exc:
        # chat transport / RateLimitError / 其他 — fallback 保護 pod 流程
        logger.warning(
            "_normalize_line_lengths 第 %d 層 LLM 呼叫失敗，atomic fallback big_topic=%s: %s",
            _depth,
            getattr(script, "topic", "?"),
            exc,
        )
        return script

    # 遞迴層數當 attempt 記，讓 cost 報表看得出第幾層切分燒掉多少 token
    _record_llm_usage(
        collector,
        ai_msg,
        node=usage_node,
        call="line_split",
        call_start=call_start,
        attempt=_depth + 1,
    )

    try:
        cleaned = _strip_code_fence(ai_msg.content)
        batch = _LineSplitBatch.model_validate_json(cleaned)
        replacements = _validate_split_batch(
            long_items, batch, target_max_words=target_max_words
        )
    except Exception as exc:
        # 解析 / 保真檢查任何失敗都 atomic fallback，切分是輔助功能不該中止 pod
        logger.warning(
            "_normalize_line_lengths 第 %d 層解析/保真檢查失敗，atomic fallback big_topic=%s: %s",
            _depth,
            getattr(script, "topic", "?"),
            exc,
        )
        return script

    candidate_lines: list[ScriptLine] = []
    for idx, original in enumerate(script.script):
        replacement = replacements.get(idx)
        if replacement is None:
            candidate_lines.append(original)
        else:
            candidate_lines.extend(replacement)

    candidate_payload = script.model_dump()
    candidate_payload["script"] = [line.model_dump() for line in candidate_lines]
    try:
        new_script = ScriptJSON.model_validate(candidate_payload)
    except PydanticValidationError as exc:
        logger.warning(
            "_normalize_line_lengths 重新組裝後 ScriptJSON 驗證失敗，atomic fallback: %s",
            exc,
        )
        return script

    # 遞迴：本輪 split 後仍有超長行 → 再丟 LLM 切一次
    still_long = [ln for ln in new_script.script if len(ln.text.split()) > target_max_words]
    if still_long and _depth + 1 < _MAX_NORMALIZE_DEPTH:
        logger.info(
            "_normalize_line_lengths 第 %d 層仍有 %d 行超長，遞迴繼續切",
            _depth,
            len(still_long),
        )
        return await _normalize_line_lengths(
            new_script,
            chat,
            target_max_words=target_max_words,
            usage_node=usage_node,
            collector=collector,
            _depth=_depth + 1,
        )
    return new_script


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
    word_floor = _word_floor(cefr, length_tier)

    best_result: ScriptJSON | None = None
    best_word_count = -1

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
                lines, seg_usage = await _generate_segment(
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
                    collector=collector,
                )
                total_usage["input_tokens"] += seg_usage["input_tokens"]
                total_usage["output_tokens"] += seg_usage["output_tokens"]
                segment_scripts.append(lines)
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
            # 短句正規化：LLM 寫稿常超過 10 字，丟回 LLM 切 2-3 行；
            # 失敗一律 atomic fallback 回傳 full_script，不影響主流程。
            # 因為覆蓋了所有初次生成與 rewrite（含 partial_rewrite）路徑，
            # 後續 judge 永遠看得到已切分版本，不需改 router / state。
            full_script = await _normalize_line_lengths(
                full_script, chat, usage_node=usage_node, collector=collector
            )
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
                "previous_segment_scripts": segment_scripts,
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
        "previous_segment_scripts": segment_scripts,
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
                script=script,
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
    script: ScriptJSON,
    feedback: list[str],
    scores: dict[str, float],
    collector: MetricsCollector | None = None,
) -> list[int]:
    """[opt-p3] 給 LLM 看整集腳本,問「哪些段是這次 judge 失敗的元兇」。

    回傳 0-indexed segment index list；失敗或 LLM 回空就回空 list
    (caller 走整輪 rewrite 邏輯)。
    """
    n_segments = len(script.script)  # 簡化:用 script 行數當段數代理
    if n_segments <= 1:
        return []  # 單段沒 partial 意義

    feedback_text = "\n".join(f"- {f}" for f in feedback[:5]) or "(no specific feedback)"
    user = (
        f"This podcast script scored these overall axes: {scores}\n"
        f"Overall feedback:\n{feedback_text}\n\n"
        f"Script (single flat list, but it's structured as ~{n_segments} segments):\n"
        f"{script.model_dump_json(indent=2)}\n\n"
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


def _source_metadata(state: PodState) -> list[dict[str, Any]]:
    """持久化來源 attribution；不把原文 text 寫進 episodes.sources。"""
    return [
        {
            "id": source.id,
            "title": source.title,
            "url": source.url,
            "provider": source.source or "",
            "source_type": "",
            "published_at": source.published_at,
        }
        for source in state.get("sources", [])
    ]


async def upsert_episode_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]
    collector = _collector(config)

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
    channel_id = state.get("channel_id")
    channel_topic_id = state.get("channel_topic_id")

    # format 是 derived（=resolve_format(topic_type, length_tier)），不重複併入 idem_key。
    # channel_id 同理絕對不可進 idem_key：canonical_topic 已足以區分內容，把 channel
    # 加進 key 會讓同一個題目在不同頻道被視為「不同集」而重複完整生成一次，白白
    # 浪費 LLM 與 TTS 配額（頻道只是這集的「歸屬／編號」屬性，不是內容維度）。
    idem_key = compute_idempotency_key(
        cluster_id=cluster_id,
        deliver_date=deliver_date,
        big_topic=big_topic,
        angle=angle,
        length_tier=length_tier,
        topic_type=topic_type,
    )
    slug = _slugify(canonical)
    script_format = state.get("format", "dialogue")
    grounded = bool(state.get("grounded"))

    usage_log = state.get("token_usage") or []
    total_in = sum(int(u.get("input_tokens", 0)) for u in usage_log)
    total_out = sum(int(u.get("output_tokens", 0)) for u in usage_log)

    # 屬於某個頻道 → 先取頻道內流水號（不屬於任何頻道時完全跳過，維持既有個人化
    # 生成路徑零開銷）。next_episode_no 失敗刻意不 try/except：這是要落庫的真實
    # 資料，不是可事後補的次要資訊，失敗就該讓整條 graph 照現有重試機制處理。
    episode_no: int | None = None
    if channel_id is not None:
        from shared.db import channels  # noqa: PLC0415 lazy import（頻道機制專用）

        episode_no = await channels.next_episode_no(channel_id)

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
        sources=_source_metadata(state),
        generation_started_at=collector.started_at if collector is not None else None,
        gen_metrics=collector.gen_metrics() if collector is not None else None,
        research_metrics=collector.research_metrics() if collector is not None else None,
        channel_id=channel_id,
        episode_no=episode_no,
    )

    run_id = ctx.get("pipeline_run_id")
    if run_id is not None and not already_rendered:
        await repo.attach_pipeline_run_episode(run_id, episode_id)

    # 頻道選題回填：只在「這次真的新產出」時才回填（already_rendered=True 代表撞到
    # 既有集，選題狀態 / 頻道進度早該在第一次成功時就已回填過，這裡不重複做）。
    # 兩個回填呼叫都必須容錯——這集已經產出來了，回填失敗是「選題庫狀態沒更新」
    # 這種可事後修的次要問題，不可讓它拖垮整條已經成功的 graph（同 2026-07-20
    # FK violation 死循環教訓：compensation / 回填一律 try/except 記 warning 就好）。
    if not already_rendered:
        if channel_topic_id is not None:
            try:
                from shared.db import channels  # noqa: PLC0415 lazy import

                await channels.update_topic_status(
                    channel_topic_id, "published", episode_id=episode_id
                )
            except Exception:
                logger.warning(
                    "頻道選題狀態回填失敗（不影響本集產出）channel_topic_id=%s episode_id=%s",
                    channel_topic_id,
                    episode_id,
                    exc_info=True,
                )
        if channel_id is not None:
            try:
                from shared.db import channels  # noqa: PLC0415 lazy import

                await channels.mark_channel_published(channel_id, deliver_date)
            except Exception:
                logger.warning(
                    "頻道出版狀態回填失敗（不影響本集產出）channel_id=%s deliver_date=%s",
                    channel_id,
                    deliver_date,
                    exc_info=True,
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
        segments, srt, cues, mp3_path = renderer.render(script_payload)
        return {
            "artifacts": EpisodeArtifacts(
                segments=segments,
                srt=srt,
                vtt="",  # mock 不產
                cues=[Cue(**c) for c in cues],
                mp3_path=mp3_path,
            ),
        }

    # production 路徑
    # workdir 不能用 auto-cleanup 的 TemporaryDirectory：每行 mp3 檔要活到
    # upload_artifacts_node（下一個 node）讀完才能刪，見 upload_artifacts_node 的 cleanup。
    workdir = make_job_workdir()
    artifacts = await render_episode(script, workdir, cefr=state.get("cefr") or "B1")
    collector = _collector(config)
    if collector is not None:
        collector.record_tts_usage(
            provider=artifacts.tts_provider, characters=artifacts.tts_characters
        )
    return {"artifacts": artifacts}


# ── Node 7: upload_artifacts ──────────────────────────────


def storage_decision(
    state: PodState, config: RunnableConfig
) -> Literal["update_keys", "dead_letter"]:
    """upload_artifacts 後分流：storage_failed → dead_letter_node → END，否則 update_keys。

    R2 上傳失敗不能留半完成 row（播放頁不能拿同 slug 舊檔冒充新音檔），也不能
    raise（會觸發 worker pgmq vt 重投 → render 整個重做）。改成 graceful END：
    decision 走 dead_letter_node 做 DELETE + 寫 errors，worker 視為完成，
    read_ct 不累積。
    """
    if not state.get("storage_failed"):
        return "update_keys"
    return "dead_letter"


async def dead_letter_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """storage_failed + 無本地 fallback → DELETE 半完成 row，graceful END。

    取代原本 update_episode_keys_node 在這情況 raise RuntimeError 的行為：
    raise 會觸發 LangGraph 整個 invoke 失敗 → worker pgmq vt 重投 → 整集
    render_episode (TTS 33s+) 重做。改 graceful END：DELETE row + 寫
    errors 標記，worker 視為完成，read_ct 不累積。

    回傳 dict 必須明確把 episode_id 清成 None：LangGraph 對沒回傳的 key 會保留
    舊值，這裡的 row 已經被 DELETE，state 裡若還留著那個 uuid，run_pod() 的
    `final.get("episode_id")` truthy 檢查就會誤判成功，把已刪除的 episode_id
    當結果回傳。
    """
    ctx = _ctx(config)
    repo = ctx.get("repo")
    idem_key = state.get("idempotency_key")
    slug = state.get("slug")
    episode_id = state.get("episode_id")
    if repo is not None and idem_key:
        await repo.delete_episode_by_idem(idem_key)
    collector = _collector(config)
    run_id = ctx.get("pipeline_run_id")
    if collector is not None:
        collector.finalize("dead_letter")
        if repo is not None and run_id is not None:
            await repo.finalize_pipeline_run(
                run_id,
                status="dead_letter",
                gen_metrics=collector.gen_metrics(),
                research_metrics=collector.research_metrics(),
            )
    logger.warning(
        "媒體雙重失敗 graceful dead-letter（id=%s slug=%s idem=%s）",
        episode_id,
        slug,
        idem_key,
    )
    return {
        "episode_id": None,
        "errors": [
            *state.get("errors", []),
            f"upload_artifacts 雙重失敗，row 已清。slug={slug}",
        ],
    }


async def upload_artifacts_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    r2 = ctx.get("r2")

    episode_id = state["episode_id"]
    art: EpisodeArtifacts = state["artifacts"]

    prefix = f"episodes/{episode_id}"
    srt_key = f"{prefix}/episode.srt"
    mp3_key = f"{prefix}/episode.mp3"

    is_production = r2 is None  # mock 路徑會注入 MockR2；production 沒有才走真 R2

    storage_failed = False
    mp3_uploaded = False

    # Phase 4：整集 mp3 接管前端播放，segments/sidecar 停產。
    try:
        if r2 is not None:
            r2.put_object(mp3_key, art.mp3_path.read_bytes(), "audio/mpeg")
            mp3_uploaded = True
            r2.put_object(srt_key, art.srt.encode("utf-8"), "application/x-subrip")
        else:
            from shared.storage import r2 as real_r2  # noqa: PLC0415

            real_r2.put_object(mp3_key, art.mp3_path.read_bytes(), "audio/mpeg")
            mp3_uploaded = True
            real_r2.put_object(srt_key, art.srt.encode("utf-8"), "application/x-subrip")
    except Exception as exc:  # 包括 StorageError 與 MockR2 forced failure
        logger.warning(
            "upload_artifacts 失敗（%s）episode_id=%s",
            exc,
            episode_id,
        )
        mp3_uploaded = False
        storage_failed = True

    # render_episode_node 用 make_job_workdir()（不會自動清）產出這些檔案，
    # 讀完就是清掉的時機。
    if is_production and art.segments:
        shutil.rmtree(art.segments[0].audio_path.parent, ignore_errors=True)

    # audio_keys 保留向後相容（介面穩定；update_episode_keys 不再用，Phase 4+1
    # 再決定是否從 PodState 與回傳 dict 拔掉）。
    return {
        "audio_keys": [],
        "mp3_key": mp3_key if mp3_uploaded and not storage_failed else None,
        "srt_key": srt_key if not storage_failed else None,
        "storage_failed": storage_failed,
    }


# ── Node 8: update_episode_keys ───────────────────────────


async def update_episode_keys_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    """storage_decision 只在 `not storage_failed` 時才會路由到這裡（見 graph.py），
    storage_failed=True 一律走 dead_letter_node → END，這裡不需要再處理雙重失敗分支。
    """
    ctx = _ctx(config)
    repo = ctx["repo"]
    collector = _collector(config)
    run_id = ctx.get("pipeline_run_id")
    art: EpisodeArtifacts = state["artifacts"]
    script: ScriptJSON = state["script"]
    # extracted_facts 現在是 SourcedFact 物件（非純字串），jsonb 落庫前先轉 dict。
    facts_payload = [f.model_dump(by_alias=False) for f in script.extracted_facts]

    if collector is not None:
        collector.finalize("success")

    # repo 是 MockRepo 或 shared.db.repo 模組，surface 相同——直接呼叫，不做 hasattr 分派。
    await repo.update_episode_keys(
        state["episode_id"],
        audio_key=state.get("mp3_key"),
        srt_key=state.get("srt_key"),
        script_json=script.model_dump(by_alias=False),
        cues=art.cues,
        extracted_facts=facts_payload,
        target_vocab=[v.model_dump(by_alias=False) for v in script.target_vocab],
        sources=_source_metadata(state),
        generation_finished_at=collector.finished_at if collector is not None else None,
        gen_metrics=collector.gen_metrics() if collector is not None else None,
    )
    if collector is not None and run_id is not None:
        await repo.finalize_pipeline_run(
            run_id,
            status="success",
            gen_metrics=collector.gen_metrics(),
            research_metrics=collector.research_metrics(),
        )
    return {}


# ── Node 9: insert_deliveries ─────────────────────────────


async def insert_deliveries_node(state: PodState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    repo = ctx["repo"]

    user_ids: list[str] = state.get("user_ids") or []
    episode_id = state["episode_id"]
    deliver_date = state["deliver_date"]
    order_id = state.get("order_id")

    for uid in user_ids:
        try:
            # insert_delivery 回傳「是否首次寫入」，直接當推送的去重閘門——
            # pipeline 重投時 ON CONFLICT DO NOTHING 回 False，不會重複通知。
            # order_id：個人點餐路徑才有值（user_ids 恆為單一使用者）；
            # 頻道批次路徑 order_id 是 None，行為與改動前一致。
            #
            # 個人點餐路徑走 app_repo.deliver_and_mark_ready：把 delivery 寫入
            # 跟 queued→ready 翻牌收進同一個 transaction，避免前端輪詢踩到
            # 「集數已交付但訂單仍 queued」的不一致窗口（race + 翻牌缺失會把
            # activeOrder 卡死直到 DB 真翻 ready 前都救不回）。
            if order_id is not None:
                inserted = await app_repo.deliver_and_mark_ready(
                    uid,
                    episode_id,
                    deliver_date,
                    order_id=order_id,
                )
            else:
                inserted = await repo.insert_delivery(
                    uid,
                    episode_id,
                    deliver_date,
                    order_id=order_id,
                )
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
            continue

        if not inserted:
            continue

        # 拿這集的對外資訊（slug + 中文標題）拼通知 payload。get_episode_meta
        # 回 None 表示 episode 已不存在（FK CASCADE 理論上不會發生，但守一下），
        # 沒有 slug 就不推。
        meta = await repo.get_episode_meta(episode_id)
        if not meta:
            continue
        try:
            await notify_user(
                uid,
                {
                    "title": f"「{meta['title']}」已製作完成",
                    "body": "點開就能聽。",
                    "url": f"/player/{meta['slug']}",
                },
            )
        except Exception as exc:
            logger.warning("交付已完成，但推播失敗（uid=%s）: %s", uid, exc)

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
            # 跑測試 / demo 時 queue 沒注入 — 走 in-process 的 backfill_dict 函式。
            from engine.pipeline.post_process import backfill_dict  # noqa: PLC0415

            await backfill_dict(script.target_vocab)
    except Exception as exc:
        logger.warning(
            "backfill_dict 失敗（不擋 generate）episode_id=%s type=%s: %s",
            state.get("episode_id"),
            type(exc).__name__,
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
