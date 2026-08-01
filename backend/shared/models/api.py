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
    # 諧音/關鍵字記憶提示（繁中，台灣在地化記憶法）：來自同一支翻譯 LLM 呼叫順便產生
    mnemonic: str | None = None


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
    # 諧音/關鍵字記憶提示：同上，來自 dict_cache JOIN
    mnemonic: str | None = None
    # 1=新字(待學習) 2=複習中(SRS) 5=精熟封存（見 migration 0026）；門檻判斷在
    # 前端算，見 VocabProvider。ge=1/le=5 對齊 UpdateVocabBody 邊界驗證，避免
    # LLM/測試寫出 0/-1/999 等垃圾值污染 API 回應。
    status: int = Field(default=1, ge=1, le=5)
    # 畢業測驗連續通過輪數；連 2 輪即精熟（status=5）。
    quiz_pass_streak: int = Field(default=0, ge=0)


class Settings(CamelModel):
    popup_enabled: bool = True
    playback_rate: float = 1.0
    theme: Literal["light", "dark", "auto"] = "auto"
    preferred_topics: list[str] = Field(default_factory=list)
    default_delivery_time: str = "07:00"  # 'HH:MM'
    # 英文難度等級：存 users.cefr_target（0001 就有的欄位，現在才真正接上），
    # 影響寫稿詞彙/句構規範、目標字數與 TTS 語速（見 nodes._CEFR_GUIDE、tts.CEFR_RATE）。
    cefr_level: Literal["A2", "B1", "B2"] = "B1"


DailyOrderStatus = Literal["pending", "queued", "ready", "played", "expired"]


class DailyOrder(CamelModel):
    id: str
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
    # status：pending（剛送出）→ queued（生成中）→ ready（生成完成，已解鎖
    # 下一筆，見 deliver_and_mark_ready）→ played（使用者實際播放完）；另有
    # expired（卡死退役，遲到交付會復活成 ready）。ready 欄位是
    # status in (ready, played) 的便捷布林。
    ready: bool = False


class WordOffset(CamelModel):
    """單字在音檔內的時間戳。單位秒，相對於 cue.start（不是 episode-global）。

    練習模式點字 → seek 到 (cue.start + word.start)。
    """

    word: str
    start: float
    end: float


class Cue(CamelModel):
    index: int
    speaker: str
    text: str
    zh: str
    start: float
    end: float
    # 詞級字幕：練習模式 word click 用。舊集 / edge-tts fallback 沒這個資料時為 None。
    # words 內的 start / end 是相對於 cue.start 的秒數（不是 episode-global）。
    words: list[WordOffset] | None = None


class Segment(CamelModel):
    """單行 mp3 對外契約：index + 已簽章的 audioUrl + 真實時長 + 在該集的時間區段。

    新方案下 audioUrl 為整集簽章時不適用（整集不再產），segments 才是前端
    Web Audio API 串接播的承載。index / start / end 對齊 Cue，duration 是
    trim 後 mp3 真實時長（秒）。前端用 index 對 Cue.binary search 結果定位
    segment、用 start / end 算 currentTime。

    word_offsets_url：詞級字幕 JSON 的已簽章 URL；舊集 / edge-tts fallback / 字幕
    抓取失敗時為 None。前端 getSegment 拿到 None 時走 cue-level click fallback。
    """

    index: int
    audio_url: str
    duration: float
    start: float
    end: float
    word_offsets_url: str | None = None


class SourceReference(CamelModel):
    """對前端暴露的來源引用：只帶 id / title / url，不暴露 text / published_at。

    URL 由 router 端過濾：只接受 http/https 開頭；javascript: / data: / file: 等
    危險 scheme 會被丟棄，避免 XSS / SSRF 風險進入前端渲染。
    """

    id: str
    title: str
    url: str


class Episode(CamelModel):
    """前端播放頁需要的集數內容。segments 由服務層一次簽章後填入。

    audio_url：整集 mp3 的簽章 URL（audio_r2_key 非空且非 segments 路徑時才有值，
    見 episode_assembly.build_episode）。雙寫過渡期 audioUrl 與 segments 會同時
    存在；前端切到單一 <audio> 元素後 segments 停產（見播放器重構 Phase 4）。
    """

    id: str  # 對外用 slug
    title: str
    title_zh: str | None = None
    topic: str
    cefr_level: str = "B1"
    cover_icon: str | None = None
    is_free: bool = False
    audio_url: str | None = None
    # 逐行 mp3 segment（雙寫過渡期保留，前端切換後停產）。
    segments: list[Segment] = Field(default_factory=list)
    cues: list[Cue] = Field(default_factory=list)
    # 來源引用：來自 DB episodes.sources（jsonb），router 過濾 http/https URL 後填入。
    # 沒來源（未 grounded 或無 provider）的集數預設空 list（不是 None，前端易處理）。
    references: list[SourceReference] = Field(default_factory=list)


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
    cover_icon: str | None = None
    is_free: bool = False
    is_featured: bool = False
    episode: int = 0
    published_at: str = ""


class RecommendedEpisode(EpisodeListItem):
    """首頁「根據你追蹤的頻道」用：EpisodeListItem 加頻道身分兩欄。"""

    channel_slug: str
    channel_name: str


# ── 頻道（Channel）機制 ─────────────────────────────────────────


class Channel(CamelModel):
    """Admin 頻道管理用完整視圖：含經營指標（episodeCount/candidateCount）。

    themePrompt 是給選題 LLM 的系統提示，只在這個 admin 視圖出現；使用者端
    一律走 ChannelPublic（刻意不含這個欄位）。coverImageUrl 是簽章後的 URL，
    不是 channels.cover_r2_key 原始值——跟 Episode.audio_url 只對外給簽章
    URL 同精神，簽章由 router 層呼叫 r2.presigned_get_url 產生。
    """

    id: str
    slug: str
    name: str
    description: str | None = None
    theme_prompt: str
    topic: str
    topic_type: str = "evergreen"
    length_tier: str = "medium"
    cefr_level: str = "B1"
    target_interval_days: int = 3
    status: str = "active"
    cover_image_url: str | None = None
    last_published_at: str | None = None
    episode_count: int = 0
    candidate_count: int = 0


class ChannelTopic(CamelModel):
    """頻道選題庫單筆項目：admin 選題審核清單用（candidate/scheduled/...）。"""

    id: str
    channel_id: str
    canonical_topic: str
    angle: str
    rationale: str | None = None
    score: float = 0.0
    status: str = "candidate"
    parent_episode_id: str | None = None
    episode_id: str | None = None
    created_at: str
    decided_at: str | None = None


class ChannelPublic(CamelModel):
    """使用者端頻道卡片：刻意不含 themePrompt（那是內部選題指令，不對外曝光）。"""

    slug: str
    name: str
    description: str | None = None
    topic: str
    cover_image_url: str | None = None
    episode_count: int = 0


# ── Ops / admin 契約（T7，Supabase JWT email 白名單，internal debug 用）───────


class AdminJobQueue(CamelModel):
    """單一 pgmq 佇列的度量（pgmq.metrics_all() 逐列對映）。

    空佇列時 pgmq 可能回 NULL age，故後三欄允許 None。
    """

    queue_name: str
    queue_length: int
    newest_msg_age_sec: int | None = None
    oldest_msg_age_sec: int | None = None
    total_messages: int | None = None


class StageMetric(CamelModel):
    """單一 LangGraph node 的耗時；來自 episodes.gen_metrics->'stages'。"""

    node: str
    duration_ms: int
    status: str
    attempt: int = 1


class AdminEpisodeStats(CamelModel):
    """單集數據頁一列：內容身分 + 播放／聽完／收藏 + 生成成本與耗時。

    listenerCount／favoriteCount 是即時跨表統計，playCount 是累積計數器
    （見 episodes.play_count，只從 migration 0023 部署後起算，無歷史）。
    """

    id: str  # 對外用 slug
    title: str
    topic: str
    cefr_level: str = "B1"
    is_free: bool = False
    episode_no: int = 0
    published_at: str = ""
    created_at: str
    channel_name: str | None = None
    has_audio: bool = False
    play_count: int = 0
    listener_count: int = 0
    favorite_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    wall_ms: int | None = None
    stages: list[StageMetric] = Field(default_factory=list)


class AdminEpisodeStatsResponse(CamelModel):
    episode_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_play_count: int = 0
    items: list[AdminEpisodeStats] = Field(default_factory=list)


class AdminLlmCall(CamelModel):
    """單次 LLM 呼叫；來自 episodes.gen_metrics->'llm_calls'。

    欄位全給預設值：gen_metrics 是 schema_version 演進中的 jsonb，舊集數
    可能缺欄位，讀取端容錯不炸。
    """

    node: str = ""
    call: str = ""
    attempt: int = 1
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    segment_index: int | None = None


class AdminTtsUsage(CamelModel):
    """TTS 用量；provider="edge" 表示 MiniMax 失敗整份 fallback（該集 TTS 免費）。"""

    provider: str = ""
    characters: int = 0


class AdminGenerationTotals(CamelModel):
    """gen_metrics->'totals'：LLM 呼叫次數與 token 合計。"""

    llm_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # [opt-p2] prompt cache 命中量。MiniMax 端若不支援 cache_control,兩者皆 0。
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0


class AdminGenerationError(CamelModel):
    """gen_metrics->'error'：失敗時的節點與訊息（成功集數為 null）。"""

    node: str = ""
    type: str = ""
    message: str = ""


class AdminResearchSummary(CamelModel):
    """episodes.research_metrics 的已知欄位；全部 optional，缺省容錯。

    來源：MetricsCollector.set_research_summary 的各節點呼叫
    （decompose / gather / cross_verify / verify_claims / judge）。
    """

    questions_count: int | None = None
    subtopics: list[str] = Field(default_factory=list)
    source_count: int | None = None
    evidence_card_count: int | None = None
    grounded: bool | None = None
    provider_counts: dict[str, int] = Field(default_factory=dict)
    verified_claim_count: int | None = None
    usable_claim_count: int | None = None
    conflict_count: int | None = None
    claim_check_total: int | None = None
    claim_check_supported: int | None = None
    claim_check_unsupported: int | None = None
    claim_check_unsupported_ratio: float | None = None
    judge_scores: dict[str, float] = Field(default_factory=dict)
    judge_verdict: str | None = None
    rewrite_iterations: int | None = None
    engine_used: str | None = None
    errors: list[str] = Field(default_factory=list)


class AdminEpisodeGeneration(CamelModel):
    """單集生成過程完整視圖：gen_metrics + research_metrics 合併。

    只在 GET /admin/episodes/{id}/generation 回傳，list 端點刻意不帶——
    llm_calls 一集可能數十筆，100 列的 list payload 會被撐爆。
    """

    status: str = ""
    enqueued_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    queue_wait_ms: int | None = None
    wall_ms: int | None = None
    tts: AdminTtsUsage | None = None
    totals: AdminGenerationTotals = Field(default_factory=AdminGenerationTotals)
    stages: list[StageMetric] = Field(default_factory=list)
    llm_calls: list[AdminLlmCall] = Field(default_factory=list)
    research: AdminResearchSummary = Field(default_factory=AdminResearchSummary)
    error: AdminGenerationError | None = None


class AdminEpsGenerateResponse(CamelModel):
    """Admin 單集生成已排入 pgmq 的確認資訊。202 僅表示已入列，不代表音檔已完成。"""

    idempotency_key: str
    msg_id: int
    status: Literal["queued"] = "queued"


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
