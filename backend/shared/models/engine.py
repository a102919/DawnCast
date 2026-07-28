"""引擎契約（snake_case）：寫稿 pipeline 內部的資料結構，對齊 script JSON ground truth。

API 契約（camelCase，鏡像前端 types.ts）在 sibling 的 api.py；兩者由 __init__.py
統一 re-export，呼叫端一律 `from shared.models import X`，不直接 import 子模組。
"""

from __future__ import annotations

import difflib
from functools import lru_cache
from typing import Literal, assert_never, get_args

import opencc
from pydantic import BaseModel, Field, model_validator

from shared.script_contract import first_duplicate_adjacent_index, missing_vocab_words


@lru_cache(maxsize=1)
def _s2t_converter() -> opencc.OpenCC:
    # 用 s2t（純字元級簡轉繁）偵測，不用 s2twp：s2twp 除了轉繁體字，還會把已經是繁體的
    # 詞彙改寫成台灣慣用詞（如 循環→迴圈），拿來當「是不是簡體字」的判準會誤殺正常
    # 繁體句子。s2t 只做字元對字元的簡繁映射，不動已經是繁體的詞。
    return opencc.OpenCC("s2t")


# s2t 的目標是 OpenCC「標準繁體」，跟台灣教育部正體字用字習慣不完全一致：這些字在台灣
# 本來就是正體慣用寫法（台/臺、唇/脣、秘/祕……），但 s2t 仍會判定為「被轉換」而誤標成
# 簡體字。前 39 字取自 opencc 套件內建 TWVariants.txt（col2，官方維護的字元變體表）；
# 台/布不在該表（屬 STCharacters.txt 的多義字判斷），是實測補上的已知誤判。
# ponytail: 白名單基於實測樣本，不保證窮盡；新誤判照這個模式補字即可，不必整套換演算法。
_TW_ACCEPTED_VARIANTS = frozenset(
    "偽啟吃嫻媯峰么抬稜簷汙洩溈潀為床痺痴皂著睪秘灶粽韁才群唇參蒍眾裡核踴缽針鯰麵顎台布"
)


def _simplified_chars_in(text: str) -> list[str]:
    """回傳 text 裡被 s2t（簡轉繁）轉換掉、且非台灣慣用變體字的字元，代表原文含簡體字。

    用 difflib 對齊而不是逐字 zip：即使是字元級轉換，個別字元也可能一對多，轉換前後
    長度不一定相同。
    """
    converted = _s2t_converter().convert(text)
    if converted == text:
        return []

    offenders: list[str] = []
    for tag, start, end, _, _ in difflib.SequenceMatcher(a=text, b=converted).get_opcodes():
        if tag in ("replace", "delete"):
            offenders.extend(ch for ch in text[start:end] if ch not in _TW_ACCEPTED_VARIANTS)
    return offenders


Speaker = Literal["Alex", "Sarah", "Nova"]

# 角度 taxonomy（PRD §6，存成不可變常數，不依賴 LLM 自己想角度）
ANGLES: tuple[tuple[str, str], ...] = (
    ("定義", "這是什麼、核心概念入門"),
    ("人物故事", "關鍵人物 / 真實案例切入"),
    ("常見誤解", "破除迷思、澄清誤會"),
    ("應用場景", "日常生活 / 職場怎麼用上"),
    ("歷史", "起源與演變"),
    ("對比", "與相似概念的差異"),
)
TopicType = Literal["news", "product", "evergreen", "skill"]
EpisodeCategory = Literal["tech", "business", "culture", "science"]

# 長度 tier（PRD 重新設計 §2）：短篇快訊 / 中篇標準集 / 長篇深度剖析。
LengthTier = Literal["short", "medium", "long"]

# 格式：雙主持對話（現況）/ 單人口白（新增）。由 topic_type × length_tier 自動決定
# （見 nodes.resolve_format），不開放使用者手動切換。
ScriptFormat = Literal["dialogue", "monologue"]

# 入口類型（PRD 重新設計 Phase 4）：使用者在前端三分頁選的入口，存進 daily_orders
# 後由 project_orders_to_requests 投影成 topic_requests.topic_type。三選一向使用者公開
# （news/topic/knowledge），skill 是後端保留值，前端 UI 不暴露。
EntryMode = Literal["news", "topic", "knowledge", "skill"]


def _map_entry_mode(entry_mode: EntryMode) -> TopicType:
    """入口對映的唯一轉換分支：daily_orders.entry_mode（使用者值域）→
    topic_requests.topic_type（引擎值域）。

    不轉的話 source factory / tone_map / resolve_format 全部查不到值，
    grounding 與格式選擇整條靜默失效。用 match + assert_never：新增 EntryMode
    值忘記補這裡，型別檢查器直接紅字，不會等到 runtime 才發現漏了分支。
    """
    match entry_mode:
        case "news":
            return "news"
        case "topic":
            return "product"
        case "knowledge":
            return "evergreen"
        case "skill":
            return "skill"
        case _:
            assert_never(entry_mode)


ENTRY_MODE_TO_TOPIC_TYPE: dict[EntryMode, TopicType] = {
    mode: _map_entry_mode(mode) for mode in get_args(EntryMode)
}


# ── 引擎契約（snake_case，對齊 script JSON）────────────────────────


class ScriptLine(BaseModel):
    speaker: Speaker
    text: str = Field(min_length=1)
    zh: str = Field(min_length=1)  # 每行強制有 zh —— 契約核心（PRD §0 阻塞已修）
    # chapter/話題轉換邊界：True 時時間軸計算在這行「之前」插入較長停頓。
    # 預設 False（沿用現有均一停頓行為），只有 long tier 的 chapter 分界會標 True。
    pause_before: bool = False
    # MiniMax voice_setting.emotion 逐行標註，讓語氣不再整集一個模板。刻意用 str
    # 不用 Literal[7 值]：LLM 是 prompt-instructed JSON，不是 schema-enforced structured
    # output，拼錯值不該讓整份腳本 parse 失敗——無效值在 TTS payload 組裝那層忽略即可。
    emotion: str | None = None


class TargetVocab(BaseModel):
    word: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class SourceSnippet(BaseModel):
    """真實資料來源片段：gather_evidence_node 抓回來、prompt 會編號注入。"""

    id: str = Field(min_length=1)
    title: str
    url: str
    text: str = Field(min_length=1)
    published_at: str | None = None
    source: str | None = None


class SourcedFact(BaseModel):
    """一條事實宣稱 + 引用的來源編號。source_ids 空 list = 未 grounded（安全預設）。"""

    claim: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)


class OutlineSegment(BaseModel):
    """大綱的一個段落：focus 是這段要涵蓋的子角度/具體內容，vocab_words 是
    預計在這段寫進對話的目標字彙（必須是 ScriptOutline.target_vocab 的子集）。

    沒有腳本欄位——分段擴寫的 LLM 呼叫只負責對話內容，topic/vocab/facts 已經
    在大綱定案重複送會浪費 token。
    """

    focus: str = Field(min_length=1)
    vocab_words: list[str] = Field(default_factory=list)


class ScriptOutline(BaseModel):
    """寫稿第一階段的 LLM 輸出：規劃「哪些內容切到哪幾段、各段帶哪些字彙」。

    結構性檢查：每段 vocab_words 必須是 target_vocab 裡的字（大小寫不敏感），
    避免大綱指派了根本不在字彙表裡的字給某一段——這跟 _target_vocab_appears_in_script
    互補（那個檢查「有沒有真的寫進對話」，這個檢查「大綱指派的字彙有沒有在總表裡」）。
    """

    topic: str = Field(min_length=1)
    topic_zh: str = Field(min_length=1)
    category: EpisodeCategory
    cover_icon: str | None = Field(
        default=None,
        description="LLM 自動推薦的專屬視覺圖示名稱（如 cpu, bot, trending-up, rocket, globe 等）",
    )
    extracted_facts: list[SourcedFact] = Field(min_length=1)
    target_vocab: list[TargetVocab] = Field(min_length=1)
    # 上限 8：對齊 _LENGTH_TIERS["long"].chapters=4 留成長空間，又擋下 LLM 給數十段
    # 放大下游 _generate_segment 呼叫次數的成本/DoS 面（每段都是一次 LLM 呼叫）。
    segments: list[OutlineSegment] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _segment_vocab_subset_of_target_vocab(self) -> ScriptOutline:
        target_set = {v.word.casefold() for v in self.target_vocab}
        bad: list[str] = []
        for seg in self.segments:
            for w in seg.vocab_words:
                if w.casefold() not in target_set:
                    bad.append(w)
        if bad:
            raise ValueError(f"大綱 segments 有 vocab_words 不在 target_vocab 裡：{bad}")
        return self


class ScriptJSON(BaseModel):
    """寫稿引擎的輸出契約。LLM 回應先剝 code fence 再 model_validate_json。"""

    topic: str = Field(min_length=1)
    topic_zh: str = Field(min_length=1)  # 中文標題，非逐字翻譯——LLM 直接生成自然標題
    category: EpisodeCategory
    cover_icon: str | None = Field(
        default=None,
        description="LLM 自動推薦的專屬視覺圖示名稱（如 cpu, bot, trending-up, rocket, globe 等）",
    )
    extracted_facts: list[SourcedFact] = Field(min_length=1)
    target_vocab: list[TargetVocab] = Field(min_length=1)
    script: list[ScriptLine] = Field(min_length=8)  # 太短直接判失敗
    format: ScriptFormat = "dialogue"

    @model_validator(mode="after")
    def _speakers_match_format(self) -> ScriptJSON:
        speakers = {line.speaker for line in self.script}
        if self.format == "dialogue":
            if speakers != {"Alex", "Sarah"}:
                raise ValueError("dialogue 格式必須同時包含 Alex 與 Sarah 兩位主持人")
        else:
            if speakers != {"Nova"}:
                raise ValueError("monologue 格式只能有單一角色 Nova")
        return self

    @model_validator(mode="after")
    def _no_duplicate_adjacent_zh(self) -> ScriptJSON:
        """LLM 偶爾會把 zh 句界跟 text 對不上，內容往下一行偏移，累積到最後兩個連續行
        zh 整段逐字重複（實測案例：cue 30/31、41/42）。相鄰 zh 完全相同必是這種對齊漂移，
        不會是正常寫稿結果——攔下來讓 write_script 重寫比放行更划算。

        規則實作與段落層級預檢（engine/pipeline/langgraph_pod/nodes.py）共用
        shared/script_contract.py，避免兩處邏輯漂移。
        """
        dup_idx = first_duplicate_adjacent_index([line.zh for line in self.script])
        if dup_idx is not None:
            raise ValueError(
                f"script[{dup_idx}] 與 script[{dup_idx - 1}] 的 zh 完全相同"
                "（中英對齊漂移導致重複，需要重寫）: "
                f"{self.script[dup_idx].zh!r}"
            )
        return self

    @model_validator(mode="after")
    def _target_vocab_appears_in_script(self) -> ScriptJSON:
        """target_vocab 是使用者本集要學的字，沒真的出現在對話裡就是選錯字。

        規則實作與段落層級預檢（engine/pipeline/langgraph_pod/nodes.py）共用
        shared/script_contract.py：用 lemmatize() 把腳本裡每個詞的詞形變化收集成
        候選集合，比對 target_vocab 是否命中（含片語拆字比對），避免兩處邏輯漂移。
        """
        full_text = " ".join(line.text for line in self.script)
        missing = missing_vocab_words(full_text, [v.word for v in self.target_vocab])
        if missing:
            raise ValueError(f"target_vocab 有字沒真的出現在腳本裡（含詞形變化都沒有）: {missing}")
        return self

    @model_validator(mode="after")
    def _zh_no_simplified_chars(self) -> ScriptJSON:
        offenders: list[str] = []
        for i, line in enumerate(self.script):
            bad = _simplified_chars_in(line.zh)
            if bad:
                offenders.append(f"script[{i}].zh 出現簡體字 {bad!r}: {line.zh!r}")
        if offenders:
            raise ValueError("zh 必須是台灣正體中文，偵測到簡體字：\n" + "\n".join(offenders))
        return self


class JudgeVerdict(BaseModel):
    """LLM-as-judge 輸出契約（LangGraph pod 的 quality_judge_node 用）。

    五軸 0-1 + ≤5 條 feedback；任一軸低於 quality_threshold 觸發 rewrite 迴圈。
    chemistry 只適用 dialogue 格式，monologue 稿子固定給 1.0（不計入淘汰判斷）。
    """

    hook_strength: float = Field(ge=0.0, le=1.0)
    informativeness: float = Field(ge=0.0, le=1.0)
    pacing: float = Field(ge=0.0, le=1.0)
    chemistry: float = Field(ge=0.0, le=1.0)
    groundedness: float = Field(ge=0.0, le=1.0)
    feedback: list[str] = Field(default_factory=list, max_length=5)


class ResearchQuestion(BaseModel):
    """研究問題拆解後的單一可查證問題。"""

    question: str = Field(min_length=1)
    kind: Literal["academic", "statistics", "claim_check", "history", "general"]
    requires_sources: bool = True


class EvidenceCard(BaseModel):
    """一張來源證據卡；source_ids 至少要指向一筆 SourceSnippet。"""

    id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    provider: str
    source_type: str
    is_primary: bool = False
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class VerifiedClaim(BaseModel):
    """交叉驗證後可供寫稿採用或排除的主張。"""

    claim: str = Field(min_length=1)
    supporting_source_ids: list[str] = Field(default_factory=list)
    contradicting_source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    usable: bool


class ClaimCheck(BaseModel):
    """成稿中的單一事實主張核對結果。"""

    claim: str = Field(min_length=1)
    status: Literal["supported", "unsupported", "uncertain"]
    source_ids: list[str] = Field(default_factory=list)


class ClaimVerification(BaseModel):
    """成稿事實主張的整體核對結果。"""

    checks: list[ClaimCheck] = Field(default_factory=list)
    unsupported_ratio: float = Field(ge=0.0, le=1.0)
