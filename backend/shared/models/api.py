"""API 契約（camelCase alias）：鏡像 frontend/src/api/types.ts，序列化即前端可直接吃。

改這裡的 model 後要跑 `uv run poe export-openapi` 並重生前端型別（見專案 CLAUDE.md），
contract test 會擋忘記重生的情況。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from shared.models.engine import EntryMode, LengthTier


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
    # 英文難度等級：存 users.cefr_target（0001 就有的欄位，現在才真正接上），
    # 影響寫稿詞彙/句構規範、目標字數與 TTS 語速（見 nodes._CEFR_GUIDE、tts.CEFR_RATE）。
    cefr_level: Literal["A2", "B1", "B2"] = "B1"


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
    """前端播放頁需要的集數內容。audioUrl 由服務層產簽章 URL 後填入。"""

    id: str  # 對外用 slug
    title: str
    title_zh: str | None = None
    topic: str
    cefr_level: str = "B1"
    is_free: bool = False
    audio_url: str | None = None
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
    """集數列表項，鏡像前端 MockEpisode（列表頁用，不含 cues / audioUrl）。

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


# ── Ops / admin 契約（T7，X-Admin-Token 驗證，internal debug 用）───────


class AdminEpisode(CamelModel):
    """admin debug 用集數清單項。hasAudio 用 audio_r2_key 是否已寫入代理生成完成訊號。"""

    id: str  # 對外用 slug
    title: str
    topic: str
    cefr_level: str = "B1"
    is_free: bool = False
    is_featured: bool = False
    episode_no: int = 0
    published_at: str = ""
    created_at: str
    freshness_class: str = "evergreen"
    expires_at: str | None = None
    has_audio: bool = False


class AdminJobQueue(CamelModel):
    """單一 pgmq 佇列的度量（pgmq.metrics_all() 逐列對映）。

    空佇列時 pgmq 可能回 NULL age，故後三欄允許 None。
    """

    queue_name: str
    queue_length: int
    newest_msg_age_sec: int | None = None
    oldest_msg_age_sec: int | None = None
    total_messages: int | None = None


class AdminTokenUsageItem(CamelModel):
    slug: str
    title: str
    input_tokens: int = 0
    output_tokens: int = 0
    created_at: str


class AdminTokenUsageResponse(CamelModel):
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    episode_count: int = 0
    items: list[AdminTokenUsageItem] = Field(default_factory=list)


# ── 帳號自我管理（T4）──────────────────────────────────────────


class AccountInfo(CamelModel):
    """GET /me 回傳欄位。id / email / tz / delivery_time / created_at。

    email 從 JWT payload 解（Supabase 預設 JWT 帶 email claim）；
    其餘欄位從 public.users SELECT。handle_new_user trigger 尚未補列時，
    tz / delivery_time / created_at 採 DB 預設值，router 端不必補空字串。
    """

    id: str
    email: str = ""  # JWT 無 email claim 時回空字串（不丟錯）
    tz: str = "Asia/Taipei"
    delivery_time: str = "07:00"
    created_at: str = ""  # ISO 8601；空字串表示尚無列（前端可顯示「剛建立」）
