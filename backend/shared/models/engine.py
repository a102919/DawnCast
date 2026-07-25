"""引擎契約（snake_case）：寫稿 pipeline 內部的資料結構，對齊 script JSON ground truth。

API 契約（camelCase，鏡像前端 types.ts）在 sibling 的 api.py；兩者由 __init__.py
統一 re-export，呼叫端一律 `from shared.models import X`，不直接 import 子模組。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from shared.lemmatize import lemmatize

_WORD_RE = re.compile(r"[A-Za-z']+")

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
FreshnessClass = Literal["evergreen", "timely", "dated"]
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


# ── 引擎契約（snake_case，對齊 script JSON）────────────────────────


class ScriptLine(BaseModel):
    speaker: Speaker
    text: str = Field(min_length=1)
    zh: str = Field(min_length=1)  # 每行強制有 zh —— 契約核心（PRD §0 阻塞已修）
    # chapter/話題轉換邊界：True 時 concat_segments 在這行「之前」插入較長停頓。
    # 預設 False（沿用現有均一停頓行為），只有 long tier 的 chapter 分界會標 True。
    pause_before: bool = False


class TargetVocab(BaseModel):
    word: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class SourceSnippet(BaseModel):
    """真實資料來源片段：retrieve_sources_node 抓回來、prompt 會編號注入。"""

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
        """
        for i in range(1, len(self.script)):
            if self.script[i].zh == self.script[i - 1].zh:
                raise ValueError(
                    f"script[{i}] 與 script[{i - 1}] 的 zh 完全相同"
                    "（中英對齊漂移導致重複，需要重寫）: "
                    f"{self.script[i].zh!r}"
                )
        return self

    @model_validator(mode="after")
    def _target_vocab_appears_in_script(self) -> ScriptJSON:
        """target_vocab 是使用者本集要學的字，沒真的出現在對話裡就是選錯字。

        用既有的 lemmatize()（shared/lemmatize.py，dict_cache 本來就在用）把腳本裡
        每個詞的詞形變化收集成候選集合，比對 target_vocab 是否命中。lemmatize() 一律
        把原 surface word 放候選清單第一位，所以 lemma_pool 本身就包含全文每個字面
        token；片語（含空白/連字號，如 "cancel out"）拆成單字後逐一比對同一個
        lemma_pool 即可——不用整段字串比對，這樣才能吃到片語動詞的詞形變化
        （"cancels the noise out" 裡的 "cancels" 會 lemma 成 "cancel"），也不會因為
        對話把片語拆開講（動詞跟受詞插在片語中間）而誤判成沒出現。
        """
        full_text_lower = " ".join(line.text for line in self.script).lower()
        lemma_pool: set[str] = set()
        for token in _WORD_RE.findall(full_text_lower):
            lemma_pool.update(lemmatize(token))

        missing: list[str] = []
        for v in self.target_vocab:
            word_lower = v.word.lower()
            if " " in word_lower or "-" in word_lower:
                parts = [p for p in re.split(r"[\s-]+", word_lower) if p]
                if not all(p in lemma_pool for p in parts):
                    missing.append(v.word)
            elif word_lower not in lemma_pool:
                missing.append(v.word)

        if missing:
            raise ValueError(f"target_vocab 有字沒真的出現在腳本裡（含詞形變化都沒有）: {missing}")
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
