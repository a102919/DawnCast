import { z } from 'zod'
import type {
  AccountInfo,
  Activity,
  Api,
  DailyOrder,
  DailyOrderStatus,
  DictEntry,
  Settings,
  VocabItem,
} from './types'
import type { Episode } from '../types/episode'
import type { MockEpisode } from '../routes/episodeData'
import { getAccessToken } from '../lib/supabaseClient'

// ─── 錯誤型別 ──────────────────────────────────────────────────────────────

export class AppError extends Error {
  readonly code: string
  readonly statusCode?: number
  constructor(code: string, message: string, statusCode?: number) {
    super(message)
    this.name = 'AppError'
    this.code = code
    this.statusCode = statusCode
  }
}

// ─── 設定 ─────────────────────────────────────────────────────────────────

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

// getLastOrderDate / setLastOrderDate 屬純 UI 狀態，留在 localStorage（後端聖約外）。
const LAST_ORDER_DATE_KEY = 'dawncast:lastOrderDate'

// ─── Envelope 解包 ─────────────────────────────────────────────────────────

const ErrorEnvelopeSchema = z.object({
  code: z.string(),
  message: z.string(),
})

const EnvelopeSchema = z.object({
  ok: z.boolean(),
  data: z.unknown(),
  error: ErrorEnvelopeSchema.nullable(),
})

type RequestOptions = {
  readonly method?: string
  readonly body?: unknown
  /** 預期回應 data 的 schema；無內容（Promise<void>）時傳 null */
  readonly schema: z.ZodType | null
  /** true 時 404/data===null 回 null 而非丟錯（lookupDict / getDailyOrder 用） */
  readonly nullable?: boolean
}

async function request<T>(path: string, opts: RequestOptions): Promise<T> {
  const token = await getAccessToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 30_000)

  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: opts.method ?? 'GET',
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: controller.signal,
    })
  } catch (err) {
    throw new AppError('network_error', err instanceof Error ? err.message : '網路錯誤')
  } finally {
    clearTimeout(timeout)
  }

  // 404 + nullable：視為「查無資料」回 null
  if (res.status === 404 && opts.nullable) {
    return null as T
  }

  const json: unknown = await res.json().catch(() => null)
  const parsed = EnvelopeSchema.safeParse(json)
  if (!parsed.success) {
    throw new AppError('invalid_response', `回應格式錯誤（${res.status}）`, res.status)
  }
  const env = parsed.data

  if (!env.ok) {
    if (opts.nullable && res.status === 404) return null as T
    const e = env.error
    throw new AppError(e?.code ?? 'unknown', e?.message ?? '請求失敗', res.status)
  }

  // 無內容方法：data=null 對齊 Promise<void>
  if (opts.schema === null) return null as T

  if (env.data === null) {
    if (opts.nullable) return null as T
    throw new AppError('empty_data', '回應缺少資料', res.status)
  }

  const dataParsed = opts.schema.safeParse(env.data)
  if (!dataParsed.success) {
    throw new AppError('schema_mismatch', '回應資料結構不符', res.status)
  }
  return dataParsed.data as T
}

// ─── 邊界 schema（對齊 types.ts，camelCase）──────────────────────────────────

const DictEntrySchema = z.object({
  word: z.string(),
  ipa: z.string().nullable().optional(),
  pos: z.array(z.string()),
  translation: z.string(),
  exchange: z.string().nullable().optional(),
  audioUrl: z.string().nullable().optional(),
  exampleEn: z.string().nullable().optional(),
  exampleZh: z.string().nullable().optional(),
}) satisfies z.ZodType<DictEntry>

const VocabItemSchema = z.object({
  id: z.string(),
  word: z.string(),
  lemma: z.string(),
  pos: z.string().nullable().optional(),
  translation: z.string(),
  ipa: z.string().nullable().optional(),
  sourceEpisodeId: z.string(),
  sourceLineNo: z.number(),
  sourceTimestamp: z.number(),
  createdAt: z.string(),
  senseIdx: z.number(),
  sourceSentence: z.string().nullable().optional(),
  sourceSentenceZh: z.string().nullable().optional(),
  nextReview: z.string().nullable().optional(),
  interval: z.number().nullable().optional(),
  ease: z.number().nullable().optional(),
  exampleEn: z.string().nullable().optional(),
  exampleZh: z.string().nullable().optional(),
}) satisfies z.ZodType<VocabItem>

const VocabListSchema = z.array(VocabItemSchema)

const SettingsSchema = z.object({
  popupEnabled: z.boolean(),
  popupDontShowAgain: z.boolean(),
  playbackRate: z.number(),
  fontSize: z.enum(['sm', 'md', 'lg']),
  theme: z.enum(['light', 'dark', 'auto']),
  preferredTopics: z.array(z.string()),
  defaultDeliveryTime: z.string(),
}) satisfies z.ZodType<Settings>

const DailyOrderStatusSchema = z.enum(['pending', 'queued', 'played']) satisfies z.ZodType<DailyOrderStatus>

const DailyOrderSchema = z.object({
  date: z.string(),
  selectedTopics: z.array(z.string()),
  specificRequest: z.string().optional(),
  status: DailyOrderStatusSchema,
  deliveryTime: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
  playedAt: z.string().optional(),
  // Phase 4：向後相容舊 client / 舊 localStorage，兩個新欄位皆 optional。
  entryMode: z.enum(['news', 'topic', 'knowledge', 'skill']).optional(),
  lengthTier: z.enum(['short', 'medium', 'long']).optional(),
}) satisfies z.ZodType<DailyOrder>

const DailyOrderListSchema = z.array(DailyOrderSchema)

const FavoritesSchema = z.array(z.string())

const CueSchema = z.object({
  index: z.number(),
  speaker: z.string(),
  text: z.string(),
  zh: z.string(),
  start: z.number(),
  end: z.number(),
})

// audioUrl 由 /episodes/{slug}/url 補上，list 內容沒有 cues。
// server get_episode Episode model 沒設 audioUrl 欄位會回 null（不是 undefined），
// 故 .nullable() 否則 zod 在 null 時拋 schema_mismatch。
const EpisodeContentSchema = z.object({
  id: z.string(),
  title: z.string(),
  audioUrl: z.string().nullable().optional(),
  cues: z.array(CueSchema),
})

// server /episodes/{slug}/url 的 data 是字串網址本身（不是 {url: ...} 物件）。
const SignedUrlSchema = z.string()

const MockEpisodeSchema = z.object({
  id: z.string(),
  title: z.string(),
  titleZh: z.string(),
  topic: z.enum(['tech', 'business', 'culture', 'science']),
  cefrLevel: z.enum(['A2', 'B1', 'B2']),
  isFeatured: z.boolean().optional(),
  episode: z.number(),
  publishedAt: z.string(),
}) satisfies z.ZodType<MockEpisode>

const EpisodeListSchema = z.array(MockEpisodeSchema)

const ActivitySchema = z.object({
  streakDates: z.array(z.string()),
  listenMinutes: z.record(z.string(), z.number()),
  lookupCount: z.record(z.string(), z.number()),
  listenedEpisodeIds: z.array(z.string()),
  lastPlayedEpisodeId: z.string().nullable().optional(),
  lastPlayedPosition: z.number().nullable().optional(),
  lastPlayedAt: z.string().nullable().optional(),
}) satisfies z.ZodType<Activity>

// T4 帳號自我管理：後端 CamelModel 保證 camelCase；email 為空字串時仍合法（JWT 無 email claim）。
const AccountInfoSchema = z.object({
  id: z.string(),
  email: z.string(),
  tz: z.string(),
  deliveryTime: z.string(),
  createdAt: z.string(),
}) satisfies z.ZodType<AccountInfo>

// ─── 實作 ─────────────────────────────────────────────────────────────────

export const httpApi: Api = {
  async lookupDict(word) {
    // 查無字（後端回 404 或 data=null）→ 回 null
    return request<DictEntry | null>(
      `/dict/lookup?w=${encodeURIComponent(word)}`,
      { schema: DictEntrySchema, nullable: true },
    )
  },

  async addVocab(item) {
    return request<VocabItem>('/vocab', { method: 'POST', body: item, schema: VocabItemSchema })
  },

  async removeVocab(id) {
    await request<null>(`/vocab/${encodeURIComponent(id)}`, { method: 'DELETE', schema: null })
  },

  async listVocab() {
    return request<VocabItem[]>('/vocab', { schema: VocabListSchema })
  },

  async searchVocab(query) {
    return request<VocabItem[]>(
      `/vocab/search?query=${encodeURIComponent(query)}`,
      { schema: VocabListSchema },
    )
  },

  async getSettings() {
    return request<Settings>('/settings', { schema: SettingsSchema })
  },

  async updateSettings(patch) {
    return request<Settings>('/settings', { method: 'PATCH', body: patch, schema: SettingsSchema })
  },

  async resetPopupPreferences() {
    await request<null>('/settings/reset-popup', { method: 'POST', schema: null })
  },

  async clearVocab() {
    await request<null>('/vocab', { method: 'DELETE', schema: null })
  },

  async updateVocab(id, patch) {
    await request<null>(`/vocab/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch, schema: null })
  },

  async getFavorites() {
    return request<readonly string[]>('/favorites', { schema: FavoritesSchema })
  },

  async addFavorite(id) {
    await request<null>(`/favorites/${encodeURIComponent(id)}`, { method: 'POST', schema: null })
  },

  async removeFavorite(id) {
    await request<null>(`/favorites/${encodeURIComponent(id)}`, { method: 'DELETE', schema: null })
  },

  async isFavorite(id) {
    const list = await request<readonly string[]>('/favorites', { schema: FavoritesSchema })
    return list.includes(id)
  },

  async getDailyOrder(date) {
    return request<DailyOrder | null>(
      `/daily-orders/${encodeURIComponent(date)}`,
      { schema: DailyOrderSchema, nullable: true },
    )
  },

  async saveDailyOrder(order) {
    return request<DailyOrder>('/daily-orders', { method: 'PUT', body: order, schema: DailyOrderSchema })
  },

  async listDailyOrders(fromDate, toDate) {
    return request<readonly DailyOrder[]>(
      `/daily-orders?from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}`,
      { schema: DailyOrderListSchema },
    )
  },

  async markOrderPlayed(date, playedAt) {
    return request<DailyOrder | null>(
      `/daily-orders/${encodeURIComponent(date)}/played`,
      { method: 'POST', body: { playedAt }, schema: DailyOrderSchema, nullable: true },
    )
  },

  async deleteDailyOrder(date) {
    await request<null>(`/daily-orders/${encodeURIComponent(date)}`, { method: 'DELETE', schema: null })
  },

  // 純 UI 狀態，留 localStorage（後端聖約外）
  async getLastOrderDate() {
    return localStorage.getItem(LAST_ORDER_DATE_KEY)
  },

  async setLastOrderDate(date) {
    localStorage.setItem(LAST_ORDER_DATE_KEY, date)
  },

  async listEpisodes() {
    return request<readonly MockEpisode[]>('/episodes', { schema: EpisodeListSchema })
  },

  async getEpisode(slug) {
    // 先取 cues，audioUrl 再以簽章 URL 補上（兩次請求合併）。
    const content = await request<z.infer<typeof EpisodeContentSchema>>(
      `/episodes/${encodeURIComponent(slug)}`,
      { schema: EpisodeContentSchema },
    )
    const audioUrl = content.audioUrl ?? (await fetchSignedUrl(slug))
    const episode: Episode = {
      id: content.id,
      title: content.title,
      audioUrl,
      cues: content.cues,
    }
    return episode
  },

  async getDeliveredEpisode(date) {
    // 當天還沒交付（collect_open 跑了但 orchestrate/evergreen 還沒結）→ 回 null，
    // 由前端 PlayerRoute fallback 到 listEpisodes()[0]。
    const content = await request<z.infer<typeof EpisodeContentSchema> | null>(
      `/daily-orders/${encodeURIComponent(date)}/episode`,
      { schema: EpisodeContentSchema, nullable: true },
    )
    if (content === null) return null
    const audioUrl = content.audioUrl ?? (await fetchSignedUrl(content.id))
    return {
      id: content.id,
      title: content.title,
      audioUrl,
      cues: content.cues,
    }
  },

  async triggerGenerateJob(date) {
    // T1：送訂單後 fire-and-forget 觸發 worker 跑當日 pipeline。
    // 後端回 202 + envelope；前端 Promise<void> 不解析 body。
    // 失敗由呼叫端 catch（DailyOrderProvider 僅 console.warn，不打斷 setOrder）。
    await request<null>(
      `/jobs/orders/${encodeURIComponent(date)}/generate`,
      { method: 'POST', schema: null },
    )
  },

  async getActivity() {
    return request<Activity>('/activity', { schema: ActivitySchema })
  },

  async patchActivity(patch) {
    return request<Activity>('/activity', { method: 'PATCH', body: patch, schema: ActivitySchema })
  },

  async getMe() {
    return request<AccountInfo>('/me', { schema: AccountInfoSchema })
  },

  async deleteAccount() {
    await request<null>('/me', { method: 'DELETE', schema: null })
  },
}

async function fetchSignedUrl(slug: string): Promise<string> {
  // server 回 "data" 是字串網址本身；signed 就是那個字串。
  const signed = await request<string>(
    `/episodes/${encodeURIComponent(slug)}/url`,
    { schema: SignedUrlSchema },
  )
  return signed
}
