import type { Episode } from '../types/episode'
import type { MockEpisode } from '../lib/episode'

export type DictEntry = {
  readonly word: string
  readonly ipa?: string | null
  readonly pos: readonly string[]
  readonly translation: string
  readonly exchange?: string | null
  readonly audioUrl?: string | null
  readonly exampleEn?: string | null
  readonly exampleZh?: string | null
  // 諧音/關鍵字記憶提示（繁中，台灣在地化記憶法）
  readonly mnemonic?: string | null
}

export type VocabItem = {
  readonly id: string
  readonly word: string
  readonly lemma: string
  readonly pos?: string | null
  readonly translation: string
  readonly ipa?: string | null
  readonly sourceEpisodeId: string
  readonly sourceLineNo: number
  readonly sourceTimestamp: number
  readonly createdAt: string
  readonly senseIdx: number
  readonly sourceSentence?: string | null
  readonly sourceSentenceZh?: string | null
  readonly nextReview?: string | null    // ISO date 'YYYY-MM-DD'
  readonly interval?: number | null     // 天數，初始 1
  readonly ease?: number | null         // SM-2 ease factor，初始 2.5
  // 字典例句（後端 JOIN dict_cache 帶出，不存 user_vocab）
  readonly exampleEn?: string | null
  readonly exampleZh?: string | null
  // 諧音/關鍵字記憶提示：同上，來自 dict_cache JOIN
  readonly mnemonic?: string | null
  // 1=新字(待學習) 2=複習中(SRS) 5=精熟封存（migration 0026）；伺服器建立時預設 1，
  // 前端不在新增時送出（比照 nextReview/interval/ease）。門檻判斷在前端算，見 VocabProvider。
  readonly status?: number
  // 畢業測驗連續通過輪數；連 2 輪即精熟（status=5），見 lib/quiz.ts
  readonly quizPassStreak?: number
}

export type Settings = {
  readonly popupEnabled: boolean
  readonly playbackRate: number
  readonly theme: 'light' | 'dark' | 'auto'
  readonly preferredTopics: readonly string[]
  /** 出餐時間 'HH:MM'，限定 6 個 chips 之一；下單時預設帶入 */
  readonly defaultDeliveryTime: string
  /** 英文難度（CEFR），影響生成詞彙/句型與 TTS 語速；存後端 users.cefr_target */
  readonly cefrLevel: 'A2' | 'B1' | 'B2'
}

export type DailyOrderStatus = 'pending' | 'queued' | 'ready' | 'played' | 'expired'

/** 入口類型：使用者在前端三分頁選的入口。
 *  與後端 EntryMode Literal 對齊；skill 是後端保留值，前端 UI 不暴露。 */
export type EntryMode = 'news' | 'topic' | 'knowledge' | 'skill'

/** 長度 tier：使用者選的集數長度。
 *  與後端 LengthTier Literal 對齊；存在 daily_orders 與 topic_requests 兩表。 */
export type LengthTier = 'short' | 'medium' | 'long'

export type DailyOrder = {
  readonly id: string
  /** 送出日期（server 用 app 時區算出，非使用者可選）；歷史列表顯示用 */
  readonly date: string
  readonly selectedTopics: readonly string[]
  readonly specificRequest?: string | null
  readonly status: DailyOrderStatus
  /** 出餐時間 'HH:MM'，預設 '07:00' */
  readonly deliveryTime: string
  readonly createdAt: string
  readonly updatedAt: string
  readonly playedAt?: string | null
  /** Phase 4：入口類型，舊 localStorage 訂單會是 undefined，由 provider 補預設 'topic' */
  readonly entryMode?: EntryMode
  /** Phase 4：長度 tier，舊 localStorage 訂單會是 undefined，由 provider 補預設 'medium' */
  readonly lengthTier?: LengthTier
  /** queued 狀態下內容是否已生成完畢；舊 localStorage 訂單無此欄位 */
  readonly ready?: boolean
}

/** 建立新訂單的輸入：不帶 id/date/status/createdAt 等 server 決定的欄位。
 *  隨時點餐：送出即建立新訂單並立即觸發生成，沒有「編輯既有訂單」這件事。 */
export type DailyOrderInput = {
  readonly selectedTopics: readonly string[]
  readonly specificRequest?: string | null
  readonly entryMode?: EntryMode
  readonly lengthTier?: LengthTier
}

/** 學習進度上雲（T2）：streak / 聆聽分鐘 / 查詞次數 / 已聽集數 / 播放進度快照。
 *  跨裝置同步；localStorage 降級為 cache。 */
export type Activity = {
  readonly streakDates: readonly string[] // 'YYYY-MM-DD'，去重
  readonly listenMinutes: Readonly<Record<string, number>> // {'YYYY-MM': minutes}
  readonly lookupCount: Readonly<Record<string, number>> // {'YYYY-MM': count}
  readonly listenedEpisodeIds: readonly string[]
  readonly lastPlayedEpisodeId?: string | null
  readonly lastPlayedPosition?: number | null
  readonly lastPlayedAt?: string | null // ISO 8601
}

/** patchActivity(patch) 的輸入：全部是「增量」語意，只合併有給的欄位（非取代）。 */
export type ActivityPatch = {
  readonly addStreakDate?: string
  readonly addListenedEpisodeId?: string
  readonly addListenMinutes?: { readonly month: string; readonly minutes: number }
  readonly addLookupCount?: { readonly month: string; readonly count: number }
  readonly lastPlayed?: { readonly episodeId: string; readonly position: number; readonly at: string }
}

/** 帳號自我管理（T4）：GET /me 回傳欄位。
 *  email 由 JWT 解（後端拿不到時回空字串，不丟錯）。
 *  tz / deliveryTime / createdAt 從 public.users 讀；trigger 尚未補列時採預設值。 */
export type AccountInfo = {
  readonly id: string
  readonly email: string
  readonly tz: string
  readonly deliveryTime: string
  readonly createdAt: string
}

/** 單一 LangGraph node 的耗時；鏡像後端 shared/models/api.py StageMetric。 */
export type StageMetric = {
  readonly node: string
  readonly durationMs: number
  readonly status: string
  readonly attempt: number
}

/** GET /admin/episodes 明細列，鏡像後端 AdminEpisodeStats。
 *  listenerCount／favoriteCount 是即時跨表統計；playCount 是累積計數器，
 *  只從 episodes.play_count 欄位部署後起算，無歷史（見 migration 0023）。 */
export type AdminEpisodeStats = {
  readonly id: string
  readonly title: string
  readonly topic: string
  readonly cefrLevel: string
  readonly isFree: boolean
  readonly episodeNo: number
  readonly publishedAt: string
  readonly createdAt: string
  readonly channelName?: string | null
  readonly hasAudio: boolean
  readonly playCount: number
  readonly listenerCount: number
  readonly favoriteCount: number
  readonly inputTokens: number
  readonly outputTokens: number
  readonly wallMs?: number | null
  readonly stages: readonly StageMetric[]
}

/** GET /admin/episodes 回應：全站加總 + 最近 100 筆明細。 */
export type AdminEpisodeStatsResponse = {
  readonly episodeCount: number
  readonly totalInputTokens: number
  readonly totalOutputTokens: number
  readonly totalPlayCount: number
  readonly items: readonly AdminEpisodeStats[]
}

/** 單次 LLM 呼叫；鏡像後端 AdminLlmCall（gen_metrics->'llm_calls'）。 */
export type AdminLlmCall = {
  readonly node: string
  readonly call: string
  readonly attempt: number
  readonly durationMs: number
  readonly inputTokens: number
  readonly outputTokens: number
  readonly segmentIndex?: number | null
}

/** TTS 用量；provider="edge" 表示 MiniMax 失敗 fallback（該集 TTS 免費）。 */
export type AdminTtsUsage = {
  readonly provider: string
  readonly characters: number
}

export type AdminGenerationTotals = {
  readonly llmCallCount: number
  readonly inputTokens: number
  readonly outputTokens: number
  readonly cacheCreationTokens: number
  readonly cacheReadTokens: number
}

export type AdminGenerationError = {
  readonly node: string
  readonly type: string
  readonly message: string
}

/** 研究過程摘要；後端 research_metrics 已知欄位，舊集數可能全缺。 */
export type AdminResearchSummary = {
  readonly questionsCount?: number | null
  readonly subtopics: readonly string[]
  readonly sourceCount?: number | null
  readonly evidenceCardCount?: number | null
  readonly grounded?: boolean | null
  readonly providerCounts: Readonly<Record<string, number>>
  readonly verifiedClaimCount?: number | null
  readonly usableClaimCount?: number | null
  readonly conflictCount?: number | null
  readonly claimCheckTotal?: number | null
  readonly claimCheckSupported?: number | null
  readonly claimCheckUnsupported?: number | null
  readonly claimCheckUnsupportedRatio?: number | null
  readonly judgeScores: Readonly<Record<string, number>>
  readonly judgeVerdict?: string | null
  readonly rewriteIterations?: number | null
  readonly engineUsed?: string | null
  readonly errors: readonly string[]
}

/** GET /admin/episodes/{id}/generation：單集生成過程完整視圖。 */
export type AdminEpisodeGeneration = {
  readonly status: string
  readonly enqueuedAt?: string | null
  readonly startedAt?: string | null
  readonly finishedAt?: string | null
  readonly queueWaitMs?: number | null
  readonly wallMs?: number | null
  readonly tts?: AdminTtsUsage | null
  readonly totals: AdminGenerationTotals
  readonly stages: readonly StageMetric[]
  readonly llmCalls: readonly AdminLlmCall[]
  readonly research: AdminResearchSummary
  readonly error?: AdminGenerationError | null
}

export type ChannelCategory = 'tech' | 'business' | 'culture' | 'science'
export type ChannelStatus = 'active' | 'paused' | 'archived'
export type TopicType = 'news' | 'product' | 'evergreen' | 'skill'
export type CefrLevel = 'A2' | 'B1' | 'B2'

/** 頻道 admin 完整視圖，鏡像後端 shared/models/api.py Channel。
 *  回應欄位刻意是寬鬆的 string（後端就是這樣宣告的）——DB 裡的值不受前端 union 管轄，
 *  多一個狀態值不該讓整個面板 schema 驗證失敗。input 端才收斂成 union（見下）。 */
export type Channel = {
  readonly id: string
  readonly slug: string
  readonly name: string
  readonly description?: string | null
  readonly themePrompt: string
  readonly topic: string
  readonly topicType: string
  readonly lengthTier: string
  readonly cefrLevel: string
  readonly targetIntervalDays: number
  readonly status: string
  /** 已簽章的 R2 URL，不是 cover_r2_key 原始值。 */
  readonly coverImageUrl?: string | null
  readonly lastPublishedAt?: string | null
  readonly episodeCount: number
  readonly candidateCount: number
}

/** 使用者端頻道卡片，鏡像後端 ChannelPublic：刻意不含 themePrompt（內部選題指令）。 */
export type ChannelPublic = {
  readonly slug: string
  readonly name: string
  readonly description?: string | null
  readonly topic: string
  readonly coverImageUrl?: string | null
  readonly episodeCount: number
}

/** 首頁「根據你追蹤的頻道」用：MockEpisode 加頻道身分兩欄，鏡像後端 RecommendedEpisode。 */
export type RecommendedEpisode = MockEpisode & {
  readonly channelSlug: string
  readonly channelName: string
}

/** 選題庫單筆候選，鏡像後端 ChannelTopic。 */
export type ChannelTopic = {
  readonly id: string
  readonly channelId: string
  readonly canonicalTopic: string
  readonly angle: string
  readonly rationale?: string | null
  readonly score: number
  readonly status: string
  readonly parentEpisodeId?: string | null
  readonly episodeId?: string | null
  readonly createdAt: string
  readonly decidedAt?: string | null
}

/** 鏡像 CreateChannelBody。有 server-side 預設的欄位在型別上仍是必填（openapi-typescript
 *  對 default 的產出就是必填），表單本來就每個欄位都有值，不特別做成 optional。 */
export type CreateChannelInput = {
  readonly slug: string
  readonly name: string
  readonly themePrompt: string
  readonly topic: ChannelCategory
  readonly description?: string | null
  readonly topicType: TopicType
  readonly lengthTier: LengthTier
  readonly cefrLevel: CefrLevel
  readonly targetIntervalDays: number
  readonly status: ChannelStatus
}

/** 鏡像 UpdateChannelBody：全 optional，只 patch 有給的欄位。 */
export type UpdateChannelInput = {
  readonly slug?: string | null
  readonly name?: string | null
  readonly description?: string | null
  readonly themePrompt?: string | null
  readonly topic?: ChannelCategory | null
  readonly topicType?: TopicType | null
  readonly lengthTier?: LengthTier | null
  readonly cefrLevel?: CefrLevel | null
  readonly targetIntervalDays?: number | null
  readonly status?: ChannelStatus | null
}

/** 202 response：選題已入 control 佇列，實際由 worker 執行（後端 admin.eps/generate 端點同一種語意）。 */
export type ChannelPlanResponse = {
  readonly channelId: string
  readonly msgId: number
  readonly status: 'queued'
}

/** 瀏覽器 PushSubscription.toJSON() 的必要欄位（送給後端登錄推播訂閱）。 */
export type PushSubscriptionInput = {
  readonly endpoint: string
  readonly keys: {
    readonly p256dh: string
    readonly auth: string
  }
}

export interface Api {
  lookupDict(word: string): Promise<DictEntry | null>
  addVocab(item: Omit<VocabItem, 'id' | 'createdAt'>): Promise<VocabItem>
  removeVocab(id: string): Promise<void>
  listVocab(): Promise<VocabItem[]>
  searchVocab(query: string): Promise<VocabItem[]>
  getSettings(): Promise<Settings>
  updateSettings(patch: Partial<Settings>): Promise<Settings>
  clearVocab(): Promise<void>
  updateVocab(id: string, patch: Partial<Pick<VocabItem, 'nextReview' | 'interval' | 'ease' | 'status' | 'quizPassStreak'>>): Promise<void>
  // 收藏的 podcast episode
  getFavorites(): Promise<readonly string[]>
  addFavorite(id: string): Promise<void>
  removeFavorite(id: string): Promise<void>
  isFavorite(id: string): Promise<boolean>
  // 點餐（隨時可點、佇列制：同一時間僅一筆進行中訂單）
  getActiveOrder(): Promise<DailyOrder | null>
  createDailyOrder(input: DailyOrderInput): Promise<DailyOrder>
  listOrderHistory(limit?: number, before?: string): Promise<readonly DailyOrder[]>
  markOrderPlayed(id: string, playedAt: string): Promise<DailyOrder | null>
  deleteDailyOrder(id: string): Promise<void>
  // podcast episode 內容。opts.channel：可選頻道 slug，帶了只回該頻道底下的集數
  // （/channels/:slug 詳情頁用）；不帶維持既有行為（全站免費／已授權集數）。
  listEpisodes(opts?: { readonly channel?: string }): Promise<readonly MockEpisode[]>
  getEpisode(slug: string): Promise<Episode>
  // 依訂單 id 取這筆訂單交付的集數（player ?orderId= 連結用）；找不到回 null 由前端 fallback
  getDeliveredEpisode(orderId: string): Promise<Episode | null>
  // 播放次數 +1，不去重。播放頁背景呼叫，失敗不影響播放體驗。
  recordEpisodePlay(episodeId: string): Promise<void>
  // 使用者端公開頻道：探索／詳情／訂閱（JWT 認證，跟 Admin 端分開）
  listChannels(): Promise<readonly ChannelPublic[]>
  getChannel(slug: string): Promise<ChannelPublic>
  subscribeChannel(slug: string): Promise<void>
  unsubscribeChannel(slug: string): Promise<void>
  listMySubscriptions(): Promise<readonly ChannelPublic[]>
  // 首頁「根據你追蹤的頻道」：追蹤頻道裡還沒聽完的最新集數
  getRecommendedEpisodes(): Promise<readonly RecommendedEpisode[]>
  // 送訂單後 fire-and-forget 觸發 worker 跑生成 pipeline
  // （POST /jobs/orders/{orderId}/generate，後端回 202 + envelope；
  // 前端 Promise<void> 不解析 body，失敗僅 log 不 throw）
  triggerGenerateJob(orderId: string): Promise<void>
  // 學習進度上雲（T2）
  getActivity(): Promise<Activity>
  patchActivity(patch: ActivityPatch): Promise<Activity>
  // 帳號自我管理（T4）：查詢 / 刪除本人帳號
  getMe(): Promise<AccountInfo>
  deleteAccount(): Promise<void>
  // Admin 單集數據總覽：播放／聽完／收藏／token／耗時（Google OAuth JWT email 白名單）。
  getAdminEpisodeStats(): Promise<AdminEpisodeStatsResponse>
  /** 單集生成過程明細（stages／LLM 呼叫／TTS 供應商／研究摘要），dialog 開啟時才抓。 */
  getAdminEpisodeGeneration(episodeId: string): Promise<AdminEpisodeGeneration>
  // Admin 頻道管理（同一組 email 白名單）。使用者端的頻道瀏覽／訂閱是另一組
  // 走 JWT 的公開端點（見下方 listChannels 等），這裡一律加 Admin 前綴避免撞名。
  listAdminChannels(): Promise<readonly Channel[]>
  createAdminChannel(input: CreateChannelInput): Promise<Channel>
  updateAdminChannel(channelId: string, patch: UpdateChannelInput): Promise<Channel>
  /** 封面走原始 body 上傳（非 multipart），Content-Type 就是檔案自己的 MIME。 */
  uploadAdminChannelCover(channelId: string, file: File): Promise<Channel>
  /** 手動觸發選題；202 只代表已入列，候選要等 worker 跑完才會出現。 */
  planAdminChannel(channelId: string): Promise<ChannelPlanResponse>
  listAdminChannelTopics(channelId: string, status?: string): Promise<readonly ChannelTopic[]>
  /** 事後否決候選（status='rejected'）或修正標題。 */
  updateAdminChannelTopic(
    channelId: string,
    topicId: string,
    patch: { readonly status?: 'candidate' | 'rejected'; readonly canonicalTopic?: string },
  ): Promise<ChannelTopic>
  // Web Push：一台裝置一筆訂閱。「有沒有訂閱」就是通知開關狀態，沒有額外欄位。
  subscribePush(subscription: PushSubscriptionInput): Promise<void>
  unsubscribePush(endpoint: string): Promise<void>
}
