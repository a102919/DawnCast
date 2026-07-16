"""pydantic 契約：FastAPI 與 worker 共用的資料結構。

兩類：
1. 引擎契約（ScriptJSON 等）— 用 snake_case，對齊 scripts/*.json 的 ground truth。
2. API 契約（DictEntry/VocabItem/Settings/DailyOrder/Episode 等）— 用 camelCase alias
   鏡像 frontend/src/api/types.ts，序列化即前端可直接吃。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

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


# ── API 契約（camelCase alias，鏡像 frontend types.ts）─────────────


class CamelModel(BaseModel):
    """對外 JSON 用 camelCase；DB 取出的 snake_case 可用欄位名 populate。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DictEntry(CamelModel):
    word: str
    ipa: str | None = None
    pos: list[str] = Field(default_factory=list)
    translation: str
    exchange: str | None = None
    audio_url: str | None = None
    example_en: str | None = None
    example_zh: str | None = None


class VocabItem(CamelModel):
    id: str
    word: str
    lemma: str
    pos: str | None = None
    translation: str
    ipa: str | None = None
    source_episode_id: str
    source_line_no: int
    source_timestamp: float
    created_at: str
    sense_idx: int = 0
    source_sentence: str | None = None
    source_sentence_zh: str | None = None
    next_review: str | None = None  # 'YYYY-MM-DD'
    interval: int | None = None
    ease: float | None = None
    # 字典例句：來自 dict_cache JOIN，不存 user_vocab（每次讀取時拉最新值）
    example_en: str | None = None
    example_zh: str | None = None


class Settings(CamelModel):
    popup_enabled: bool = True
    popup_dont_show_again: bool = False
    playback_rate: float = 1.0
    font_size: Literal["sm", "md", "lg"] = "md"
    theme: Literal["light", "dark", "auto"] = "auto"
    preferred_topics: list[str] = Field(default_factory=list)
    default_delivery_time: str = "07:00"  # 'HH:MM'


DailyOrderStatus = Literal["pending", "queued", "played"]


class DailyOrder(CamelModel):
    date: str
    selected_topics: list[str] = Field(default_factory=list)
    specific_request: str | None = None
    status: DailyOrderStatus = "pending"
    delivery_time: str = "07:00"
    created_at: str
    updated_at: str
    played_at: str | None = None
    # Phase 4 新增：入口類型與長度 tier。預設值對齊 migration 0007 給舊列回退路徑。
    entry_mode: EntryMode = "topic"
    length_tier: LengthTier = "medium"


class Cue(CamelModel):
    index: int
    speaker: str
    text: str
    zh: str
    start: float
    end: float


class Episode(CamelModel):
    """前端播放頁需要的集數內容。videoUrl 由服務層產簽章 URL 後填入。"""

    id: str  # 對外用 slug
    title: str
    title_zh: str | None = None
    topic: str
    cefr_level: str = "B1"
    is_free: bool = False
    video_url: str | None = None
    cues: list[Cue] = Field(default_factory=list)


class Activity(CamelModel):
    """學習進度上雲（T2）。四個累積型欄位 + 播放進度快照，跨裝置同步。

    PATCH 端點做「合併」而非「取代」；此模型是合併後（或無列時的預設）快照。
    """

    streak_dates: list[str] = Field(default_factory=list)  # ["YYYY-MM-DD", ...]
    listen_minutes: dict[str, int] = Field(default_factory=dict)  # {"YYYY-MM": minutes}
    lookup_count: dict[str, int] = Field(default_factory=dict)  # {"YYYY-MM": count}
    listened_episode_ids: list[str] = Field(default_factory=list)
    last_played_episode_id: str | None = None
    last_played_position: float | None = None
    last_played_at: str | None = None  # ISO 8601


class EpisodeListItem(CamelModel):
    """集數列表項，鏡像前端 MockEpisode（列表頁用，不含 cues / videoUrl）。

    title_zh / episode / published_at 在 DB 可為 NULL，但前端 zod 要求非空，
    故查詢端一律 coalesce 出預設值（見 episodes.list_episodes）。
    """

    id: str  # 對外用 slug
    title: str
    title_zh: str = ""
    topic: str
    cefr_level: str = "B1"
    is_free: bool = False
    is_featured: bool = False
    episode: int = 0
    published_at: str = ""
