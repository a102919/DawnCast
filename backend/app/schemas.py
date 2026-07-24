"""Request body 的 pydantic 模型：外部輸入邊界驗證，失敗回 400。

回應型別重用 shared.models（DictEntry/VocabItem/Settings/DailyOrder/Episode）。
輸入用 camelCase alias 對齊前端送出的 JSON。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from shared.models import CamelModel, EntryMode, LengthTier


class AddVocabBody(CamelModel):
    """對齊前端 Omit<VocabItem,'id'|'createdAt'>。

    SM-2 欄位（nextReview/interval/ease）由 server 設預設，前端送的忽略。
    """

    word: str = Field(min_length=1)
    lemma: str = Field(min_length=1)
    pos: str | None = None
    translation: str = Field(min_length=1)
    ipa: str | None = None
    source_episode_id: str = Field(min_length=1)
    source_line_no: int
    source_timestamp: float
    sense_idx: int = 0
    source_sentence: str | None = None
    source_sentence_zh: str | None = None


class UpdateVocabBody(CamelModel):
    """updateVocab(id, patch{nextReview,interval,ease})。皆 optional。"""

    next_review: str | None = None
    interval: int | None = None
    ease: float | None = None


class UpdateSettingsBody(CamelModel):
    """updateSettings(patch: Partial<Settings>)。全 optional，只 upsert 有給的欄位。"""

    popup_enabled: bool | None = None
    playback_rate: float | None = None
    theme: Literal["light", "dark", "auto"] | None = None
    preferred_topics: list[str] | None = None
    default_delivery_time: str | None = None
    cefr_level: Literal["A2", "B1", "B2"] | None = None


class ListenMinutesDelta(CamelModel):
    """addListenMinutes 的增量輸入：指定月份要「加上」的分鐘數（非取代）。"""

    month: str = Field(min_length=1)  # 'YYYY-MM'
    minutes: int = Field(ge=0)


class LookupCountDelta(CamelModel):
    """addLookupCount 的增量輸入：指定月份要「加上」的查詞次數（非取代）。"""

    month: str = Field(min_length=1)  # 'YYYY-MM'
    count: int = Field(ge=0)


class LastPlayedInput(CamelModel):
    """播放進度快照。at 是事件發生時間（ISO 8601），用來擋亂序節流請求覆蓋新進度。"""

    episode_id: str = Field(min_length=1)
    position: float = Field(ge=0)
    at: str = Field(min_length=1)


class PatchActivityBody(CamelModel):
    """patchActivity(patch)。全 optional，皆為「增量」語意，只合併有給的欄位。"""

    add_streak_date: str | None = Field(default=None, min_length=1)
    add_listened_episode_id: str | None = Field(default=None, min_length=1)
    add_listen_minutes: ListenMinutesDelta | None = None
    add_lookup_count: LookupCountDelta | None = None
    last_played: LastPlayedInput | None = None


class SaveDailyOrderBody(CamelModel):
    """saveDailyOrder(order)。前端送完整 DailyOrder；date 為 key。"""

    date: str = Field(min_length=1)
    selected_topics: list[str] = Field(default_factory=list)
    specific_request: str | None = None
    status: Literal["pending", "queued", "played"] = "pending"
    delivery_time: str = "07:00"
    played_at: str | None = None
    # Phase 4：寫入端也帶入口類型與長度 tier；不送時靠 DB DEFAULT fallback（migration 0007）。
    entry_mode: EntryMode = "topic"
    length_tier: LengthTier = "medium"


class MarkPlayedBody(CamelModel):
    """markOrderPlayed(date, playedAt) 的 body 部分（date 走 path）。"""

    played_at: str = Field(min_length=1)
