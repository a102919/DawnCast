"""引擎契約（snake_case）：寫稿 pipeline 內部的資料結構，對齊 script JSON ground truth。

API 契約（camelCase，鏡像前端 types.ts）在 sibling 的 api.py；兩者由 __init__.py
統一 re-export，呼叫端一律 `from shared.models import X`，不直接 import 子模組。
"""


from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


class SourcedFact(BaseModel):
    """一條事實宣稱 + 引用的來源編號。source_ids 空 list = 未 grounded（安全預設）。"""

    claim: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)


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
