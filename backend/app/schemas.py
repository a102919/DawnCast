"""Request body 的 pydantic 模型：外部輸入邊界驗證，失敗回 400。

回應型別重用 shared.models（DictEntry/VocabItem/Settings/DailyOrder/Episode）。
輸入用 camelCase alias 對齊前端送出的 JSON。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from shared.models import CamelModel, EntryMode, LengthTier, TopicType


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
    """updateVocab(id, patch{nextReview,interval,ease,status})。皆 optional。"""

    next_review: str | None = None
    interval: int | None = None
    ease: float | None = None
    # 1=new..5=ignored（精熟）；對齊 migration 0001 註解與 DB default。
    # 前端 VocabProvider.nextStatus 從 quality 算出 status，沒驗 → 直接繞過門檻
    # 把卡片標成 mastered，必須在邊界層擋下。
    status: int | None = Field(default=None, ge=1, le=5)


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


class PushSubscriptionKeys(BaseModel):
    """PushSubscription.toJSON().keys —— payload 加密用的兩把金鑰。

    刻意用 BaseModel 而非 CamelModel：欄位名由 W3C Push API 規格決定
    （瀏覽器就是給 p256dh），套 to_camel 會變成 p256Dh 對不上。
    """

    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)


class PushUnsubscribeBody(CamelModel):
    """取消訂閱只認 endpoint（PK）。"""

    # push service 網址一律 https；長度上限防呆，避免塞超長字串進 PK。
    endpoint: str = Field(min_length=1, max_length=2048, pattern=r"^https://")


class PushSubscribeBody(PushUnsubscribeBody):
    """瀏覽器 PushSubscription.toJSON()（endpoint + keys）。"""

    keys: PushSubscriptionKeys


class AdminEpsGenerateBody(CamelModel):
    """admin 直接排入單集生成；繞過 daily_order / control orchestration。

    落庫時 is_free=True（公開，登入即可見）由 source="fallback" 自動推導，
    呼叫端不用傳。
    """

    topic: str = Field(min_length=1)
    angle: Literal[
        "定義",
        "人物故事",
        "常見誤解",
        "應用場景",
        "歷史",
        "對比",
    ] = "定義"
    topic_type: TopicType = "evergreen"
    length_tier: LengthTier = "medium"
    cefr: Literal["A2", "B1", "B2"] = "B1"
    user_ids: list[str] = Field(default_factory=list)
    deliver_date: str | None = None
