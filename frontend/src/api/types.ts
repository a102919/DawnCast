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
}

export type Settings = {
  popupEnabled: boolean
  popupDontShowAgain: boolean
  playbackRate: number
  fontSize: 'sm' | 'md' | 'lg'
  theme: 'light' | 'dark' | 'auto'
  readonly preferredTopics: readonly string[]
  /** 出餐時間 'HH:MM'，限定 6 個 chips 之一；下單時預設帶入 */
  readonly defaultDeliveryTime: string
  /** 英文難度（CEFR），影響生成詞彙/句型與 TTS 語速；存後端 users.cefr_target */
  readonly cefrLevel: 'A2' | 'B1' | 'B2'
}

export type DailyOrderStatus = 'pending' | 'queued' | 'played'

/** 入口類型：使用者在前端三分頁選的入口。
 *  與後端 EntryMode Literal 對齊；skill 是後端保留值，前端 UI 不暴露。 */
export type EntryMode = 'news' | 'topic' | 'knowledge' | 'skill'

/** 長度 tier：使用者選的集數長度。
 *  與後端 LengthTier Literal 對齊；存在 daily_orders 與 topic_requests 兩表。 */
export type LengthTier = 'short' | 'medium' | 'long'

export type DailyOrder = {
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
}

/** 寫入時不需要 date / createdAt / updatedAt / playedAt，provider 補齊 */
export type DailyOrderInput = {
  readonly selectedTopics: readonly string[]
  readonly specificRequest?: string | null
  readonly status?: DailyOrderStatus
  readonly deliveryTime: string
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

export interface Api {
  lookupDict(word: string): Promise<DictEntry | null>
  addVocab(item: Omit<VocabItem, 'id' | 'createdAt'>): Promise<VocabItem>
  removeVocab(id: string): Promise<void>
  listVocab(): Promise<VocabItem[]>
  searchVocab(query: string): Promise<VocabItem[]>
  getSettings(): Promise<Settings>
  updateSettings(patch: Partial<Settings>): Promise<Settings>
  resetPopupPreferences(): Promise<void>
  clearVocab(): Promise<void>
  updateVocab(id: string, patch: Partial<Pick<VocabItem, 'nextReview' | 'interval' | 'ease'>>): Promise<void>
  // 收藏的 podcast episode
  getFavorites(): Promise<readonly string[]>
  addFavorite(id: string): Promise<void>
  removeFavorite(id: string): Promise<void>
  isFavorite(id: string): Promise<boolean>
  // 每日點餐
  getDailyOrder(date: string): Promise<DailyOrder | null>
  saveDailyOrder(order: DailyOrder): Promise<DailyOrder>
  listDailyOrders(fromDate: string, toDate: string): Promise<readonly DailyOrder[]>
  markOrderPlayed(date: string, playedAt: string): Promise<DailyOrder | null>
  deleteDailyOrder(date: string): Promise<void>
  getLastOrderDate(): Promise<string | null>
  setLastOrderDate(date: string): Promise<void>
  // podcast episode 內容
  listEpisodes(): Promise<readonly MockEpisode[]>
  getEpisode(slug: string): Promise<Episode>
  // 依日期取當日交付的集數（player ?date= 連結用）；找不到回 null 由前端 fallback
  getDeliveredEpisode(date: string): Promise<Episode | null>
  // T1：送訂單後 fire-and-forget 觸發 worker 跑當日 pipeline（POST /jobs/orders/{date}/generate，
  // 後端回 202 + envelope；前端 Promise<void> 不解析 body，失敗僅 log 不 throw）
  triggerGenerateJob(date: string): Promise<void>
  // 學習進度上雲（T2）
  getActivity(): Promise<Activity>
  patchActivity(patch: ActivityPatch): Promise<Activity>
  // 帳號自我管理（T4）：查詢 / 刪除本人帳號
  getMe(): Promise<AccountInfo>
  deleteAccount(): Promise<void>
}
