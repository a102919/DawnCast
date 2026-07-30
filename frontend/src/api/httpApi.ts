import { z } from 'zod'
import type {
  AccountInfo,
  Activity,
  AdminEpisodeGeneration,
  AdminEpisodeStats,
  AdminEpisodeStatsResponse,
  Api,
  Channel,
  ChannelPlanResponse,
  ChannelPublic,
  ChannelTopic,
  CreateChannelInput,
  DailyOrder,
  DailyOrderStatus,
  DictEntry,
  PushSubscriptionInput,
  RecommendedEpisode,
  Settings,
  UpdateChannelInput,
  VocabItem,
} from './types'
import type { components } from './generated'
import type { Cue, Episode, SourceReference } from '../types/episode'
import type { MockEpisode } from '../lib/episode'
import { getAccessToken } from '../lib/supabaseClient'

// components['schemas'][X] 是後端 backend/shared/models.py 的唯一事實來源
// （由 `uv run poe export-openapi && npm run gen:api-types` 產生，見 generated.ts）。
// 下面每個 zod schema 的 `satisfies` 同時釘住「前端手寫型別」與「後端實際契約」兩邊——
// 後端改欄位名/型別但前端忘記跟著改時，這裡會直接編譯錯誤，不用等到 runtime 才發現撈不到資料。

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

// admin 端點共用 header。X-Admin-Token 後門已於 2026-07-29 砍掉，現僅留 email
// 白名單這條路（後端從既有的 Authorization Bearer JWT 推 email）。
// 保留 extraHeaders 介面純粹是為了 admin 系列函式的 signature 一致——其值對
// 後端已無作用，但拿掉會逼著整批 admin 函式改 signature，純 churn。
function adminHeaders(): Record<string, string> {
  return {}
}

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
  /** 額外 header（admin token 等）；與既有 Authorization 並存 */
  readonly extraHeaders?: Readonly<Record<string, string>>
  /** 原始 body（封面上傳）：給定時不做 JSON.stringify，Content-Type 用檔案自己的 MIME。 */
  readonly rawBody?: { readonly data: BodyInit; readonly contentType: string }
}

function requestBody(opts: RequestOptions): BodyInit | undefined {
  if (opts.rawBody) return opts.rawBody.data
  return opts.body === undefined ? undefined : JSON.stringify(opts.body)
}

async function request<T>(path: string, opts: RequestOptions): Promise<T> {
  const token = await getAccessToken()
  const headers: Record<string, string> = { 'Content-Type': opts.rawBody?.contentType ?? 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  if (opts.extraHeaders) Object.assign(headers, opts.extraHeaders)

  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 30_000)

  let res: Response
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      method: opts.method ?? 'GET',
      headers,
      body: requestBody(opts),
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
  mnemonic: z.string().nullable().optional(),
}) satisfies z.ZodType<DictEntry> & z.ZodType<components['schemas']['DictEntry']>

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
  mnemonic: z.string().nullable().optional(),
  // 過渡期容錯：後端欄位剛上線、rolling deploy 期間舊實例不會回 status，否則 zod
  // schema_mismatch 直接讓整個 VocabRoute + SessionRoute 空白。default(1) 對齊
  // backend VocabItem.status 預設值。
  status: z.number().default(1),
  // 同上過渡期容錯：舊實例不回 quizPassStreak 時以 0 起算。
  quizPassStreak: z.number().default(0),
}) satisfies z.ZodType<VocabItem> & z.ZodType<components['schemas']['VocabItem']>

const VocabListSchema = z.array(VocabItemSchema)

const SettingsSchema = z.object({
  popupEnabled: z.boolean(),
  playbackRate: z.number(),
  theme: z.enum(['light', 'dark', 'auto']),
  preferredTopics: z.array(z.string()),
  defaultDeliveryTime: z.string(),
  cefrLevel: z.enum(['A2', 'B1', 'B2']),
}) satisfies z.ZodType<Settings> & z.ZodType<components['schemas']['Settings']>

const DailyOrderStatusSchema = z.enum(['pending', 'queued', 'ready', 'played']) satisfies z.ZodType<DailyOrderStatus>

const DailyOrderSchema = z.object({
  id: z.string(),
  date: z.string(),
  selectedTopics: z.array(z.string()),
  specificRequest: z.string().nullable().optional(),
  status: DailyOrderStatusSchema,
  deliveryTime: z.string(),
  createdAt: z.string(),
  updatedAt: z.string(),
  playedAt: z.string().nullable().optional(),
  // 後端 DailyOrder model 兩欄皆有 DB default（見 lessons.md 2026-07-15），
  // 這裡驗的是即時 HTTP 回應（見下方呼叫端），不是 localStorage 舊單快取，
  // 故不用再放寬——真的缺欄位應該讓 zod 直接炸，而不是默默補 undefined。
  entryMode: z.enum(['news', 'topic', 'knowledge', 'skill']),
  lengthTier: z.enum(['short', 'medium', 'long']),
  ready: z.boolean(),
}) satisfies z.ZodType<DailyOrder> & z.ZodType<components['schemas']['DailyOrder']>

const DailyOrderListSchema = z.array(DailyOrderSchema)

const FavoritesSchema = z.array(z.string())

export const CueSchema = z.object({
  index: z.number(),
  speaker: z.string(),
  text: z.string(),
  zh: z.string(),
  start: z.number(),
  end: z.number(),
}) satisfies z.ZodType<Cue> & z.ZodType<components['schemas']['Cue']>

/** 單行 mp3 對前端契約：index + 已簽章 audioUrl + 真實時長 + 在該集的時間區段。
 *  mock 模式 consumer（mockApi）也用這份，避免重複定義 schema 導致型別漂移。 */
export const SegmentSchema = z.object({
  index: z.number(),
  audioUrl: z.string(),
  duration: z.number(),
  start: z.number(),
  end: z.number(),
}) satisfies z.ZodType<components['schemas']['Segment']>

/** 資料來源連結 schema。 */
export const SourceReferenceSchema = z.object({
  id: z.string(),
  title: z.string(),
  url: z.string(),
}) satisfies z.ZodType<SourceReference> & z.ZodType<components['schemas']['SourceReference']>

// audioUrl 來自舊 audio_r2_key 簽章（新方案下後端可能 null）；segments 才是
// 新路徑，前端 useSegmentPlayer hook 用 segments 串接播。兩欄都收、後端 null
// 或空 list 都 graceful（前端 consumer 各自判斷）。
// titleZh/topic/cefrLevel/isFree 後端本來就會送（見 shared/models.py Episode），
// 前端目前用不到但要收進來，不然 satisfies 抓不到後端這幾欄之後改型別/改名。
// references 後端已送（見 shared/models/api.py Episode.references），empty
// list 視為無來源；保留 optional 是為了對齊舊測試/mock 資料可能缺欄位的情境。
const EpisodeContentSchema = z.object({
  id: z.string(),
  title: z.string(),
  titleZh: z.string().nullable().optional(),
  topic: z.string(),
  cefrLevel: z.string(),
  isFree: z.boolean(),
  audioUrl: z.string().nullable().optional(),
  coverIcon: z.string().nullable().optional(),
  segments: z.array(SegmentSchema).default([]),
  cues: z.array(CueSchema),
  references: z.array(SourceReferenceSchema).optional(),
}) satisfies z.ZodType<components['schemas']['Episode']>

// server /episodes/{slug}/url 的 data 是字串網址本身（不是 {url: ...} 物件）。
// 新方案下不再使用此端點（EpisodeContentSchema 一次回齊 segments[] 簽章 URL）；
// 函式 fetchSignedUrl 已移除，避免 Phase G 之前還有人誤用舊路徑。

const MockEpisodeSchema = z.object({
  id: z.string(),
  title: z.string(),
  titleZh: z.string(),
  topic: z.enum(['tech', 'business', 'culture', 'science']),
  cefrLevel: z.enum(['A2', 'B1', 'B2']),
  // isFree：後端 EpisodeListItem 本來就會送，前端 MockEpisode 目前沒有消費它
  // （不是本次範圍要加的 UI 功能），但既然後端送了就該收進來驗證，不能悄悄丟掉。
  isFree: z.boolean(),
  isFeatured: z.boolean(),
  episode: z.number(),
  publishedAt: z.string(),
  coverIcon: z.string().nullable().optional(),
}) satisfies z.ZodType<MockEpisode> & z.ZodType<components['schemas']['EpisodeListItem']>

const EpisodeListSchema = z.array(MockEpisodeSchema)

const ActivitySchema = z.object({
  streakDates: z.array(z.string()),
  listenMinutes: z.record(z.string(), z.number()),
  lookupCount: z.record(z.string(), z.number()),
  listenedEpisodeIds: z.array(z.string()),
  lastPlayedEpisodeId: z.string().nullable().optional(),
  lastPlayedPosition: z.number().nullable().optional(),
  lastPlayedAt: z.string().nullable().optional(),
}) satisfies z.ZodType<Activity> & z.ZodType<components['schemas']['Activity']>

// T4 帳號自我管理：後端 CamelModel 保證 camelCase；email 為空字串時仍合法（JWT 無 email claim）。
const AccountInfoSchema = z.object({
  id: z.string(),
  email: z.string(),
  tz: z.string(),
  deliveryTime: z.string(),
  createdAt: z.string(),
}) satisfies z.ZodType<AccountInfo> & z.ZodType<components['schemas']['AccountInfo']>

const StageMetricSchema = z.object({
  node: z.string(),
  durationMs: z.number(),
  status: z.string(),
  attempt: z.number(),
}) satisfies z.ZodType<components['schemas']['StageMetric']>

const AdminEpisodeStatsSchema = z.object({
  id: z.string(),
  title: z.string(),
  topic: z.string(),
  cefrLevel: z.string(),
  isFree: z.boolean(),
  episodeNo: z.number(),
  publishedAt: z.string(),
  createdAt: z.string(),
  channelName: z.string().nullable().optional(),
  hasAudio: z.boolean(),
  playCount: z.number(),
  listenerCount: z.number(),
  favoriteCount: z.number(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  wallMs: z.number().nullable().optional(),
  stages: z.array(StageMetricSchema),
}) satisfies z.ZodType<AdminEpisodeStats> & z.ZodType<components['schemas']['AdminEpisodeStats']>

const AdminLlmCallSchema = z.object({
  node: z.string(),
  call: z.string(),
  attempt: z.number(),
  durationMs: z.number(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  segmentIndex: z.number().nullable().optional(),
}) satisfies z.ZodType<components['schemas']['AdminLlmCall']>

const AdminTtsUsageSchema = z.object({
  provider: z.string(),
  characters: z.number(),
}) satisfies z.ZodType<components['schemas']['AdminTtsUsage']>

const AdminGenerationTotalsSchema = z.object({
  llmCallCount: z.number(),
  inputTokens: z.number(),
  outputTokens: z.number(),
  cacheCreationTokens: z.number(),
  cacheReadTokens: z.number(),
}) satisfies z.ZodType<components['schemas']['AdminGenerationTotals']>

const AdminGenerationErrorSchema = z.object({
  node: z.string(),
  type: z.string(),
  message: z.string(),
}) satisfies z.ZodType<components['schemas']['AdminGenerationError']>

const AdminResearchSummarySchema = z.object({
  questionsCount: z.number().nullable().optional(),
  subtopics: z.array(z.string()),
  sourceCount: z.number().nullable().optional(),
  evidenceCardCount: z.number().nullable().optional(),
  grounded: z.boolean().nullable().optional(),
  providerCounts: z.record(z.string(), z.number()),
  verifiedClaimCount: z.number().nullable().optional(),
  usableClaimCount: z.number().nullable().optional(),
  conflictCount: z.number().nullable().optional(),
  claimCheckTotal: z.number().nullable().optional(),
  claimCheckSupported: z.number().nullable().optional(),
  claimCheckUnsupported: z.number().nullable().optional(),
  claimCheckUnsupportedRatio: z.number().nullable().optional(),
  judgeScores: z.record(z.string(), z.number()),
  judgeVerdict: z.string().nullable().optional(),
  rewriteIterations: z.number().nullable().optional(),
  engineUsed: z.string().nullable().optional(),
  errors: z.array(z.string()),
}) satisfies z.ZodType<components['schemas']['AdminResearchSummary']>

const AdminEpisodeGenerationSchema = z.object({
  status: z.string(),
  enqueuedAt: z.string().nullable().optional(),
  startedAt: z.string().nullable().optional(),
  finishedAt: z.string().nullable().optional(),
  queueWaitMs: z.number().nullable().optional(),
  wallMs: z.number().nullable().optional(),
  tts: AdminTtsUsageSchema.nullable().optional(),
  totals: AdminGenerationTotalsSchema,
  stages: z.array(StageMetricSchema),
  llmCalls: z.array(AdminLlmCallSchema),
  research: AdminResearchSummarySchema,
  error: AdminGenerationErrorSchema.nullable().optional(),
}) satisfies z.ZodType<AdminEpisodeGeneration> &
  z.ZodType<components['schemas']['AdminEpisodeGeneration']>

const AdminEpisodeStatsResponseSchema = z.object({
  episodeCount: z.number(),
  totalInputTokens: z.number(),
  totalOutputTokens: z.number(),
  totalPlayCount: z.number(),
  items: z.array(AdminEpisodeStatsSchema),
}) satisfies z.ZodType<AdminEpisodeStatsResponse> & z.ZodType<components['schemas']['AdminEpisodeStatsResponse']>

const ChannelSchema = z.object({
  id: z.string(),
  slug: z.string(),
  name: z.string(),
  description: z.string().nullable().optional(),
  themePrompt: z.string(),
  topic: z.string(),
  topicType: z.string(),
  lengthTier: z.string(),
  cefrLevel: z.string(),
  targetIntervalDays: z.number(),
  status: z.string(),
  coverImageUrl: z.string().nullable().optional(),
  lastPublishedAt: z.string().nullable().optional(),
  episodeCount: z.number(),
  candidateCount: z.number(),
}) satisfies z.ZodType<Channel> & z.ZodType<components['schemas']['Channel']>

const ChannelListSchema = z.array(ChannelSchema)

const ChannelTopicSchema = z.object({
  id: z.string(),
  channelId: z.string(),
  canonicalTopic: z.string(),
  angle: z.string(),
  rationale: z.string().nullable().optional(),
  score: z.number(),
  status: z.string(),
  parentEpisodeId: z.string().nullable().optional(),
  episodeId: z.string().nullable().optional(),
  createdAt: z.string(),
  decidedAt: z.string().nullable().optional(),
}) satisfies z.ZodType<ChannelTopic> & z.ZodType<components['schemas']['ChannelTopic']>

const ChannelTopicListSchema = z.array(ChannelTopicSchema)

const ChannelPlanResponseSchema = z.object({
  channelId: z.string(),
  msgId: z.number(),
  status: z.literal('queued'),
}) satisfies z.ZodType<ChannelPlanResponse> & z.ZodType<components['schemas']['ChannelPlanResponse']>

const ChannelPublicSchema = z.object({
  slug: z.string(),
  name: z.string(),
  description: z.string().nullable().optional(),
  topic: z.string(),
  coverImageUrl: z.string().nullable().optional(),
  episodeCount: z.number(),
}) satisfies z.ZodType<ChannelPublic> & z.ZodType<components['schemas']['ChannelPublic']>

const ChannelPublicListSchema = z.array(ChannelPublicSchema)

// MockEpisodeSchema 的欄位 + 頻道身分兩欄，對齊後端 RecommendedEpisode 用繼承表達同一件事。
const RecommendedEpisodeSchema = MockEpisodeSchema.extend({
  channelSlug: z.string(),
  channelName: z.string(),
}) satisfies z.ZodType<RecommendedEpisode> & z.ZodType<components['schemas']['RecommendedEpisode']>

const RecommendedEpisodeListSchema = z.array(RecommendedEpisodeSchema)

// getEpisode / getDeliveredEpisode 共用的 EpisodeContentSchema → Episode 映射。
function toEpisode(content: z.infer<typeof EpisodeContentSchema>): Episode {
  return {
    id: content.id,
    title: content.title,
    audioUrl: content.audioUrl ?? null,
    segments: content.segments,
    cues: content.cues,
    // 「無來源」語意對齊：後端沒送 references、或送空陣列 → 不帶欄位，
    // UI 一律靠 `episode.references?.length > 0` 判斷，避免下游做兩種判斷。
    ...(content.references && content.references.length > 0
      ? { references: content.references }
      : {}),
  }
}

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

  async getActiveOrder() {
    return request<DailyOrder | null>(
      '/daily-orders/active',
      { schema: DailyOrderSchema, nullable: true },
    )
  },

  async createDailyOrder(input) {
    return request<DailyOrder>('/daily-orders', { method: 'POST', body: input, schema: DailyOrderSchema })
  },

  async listOrderHistory(limit, before) {
    const params = new URLSearchParams()
    if (limit !== undefined) params.set('limit', String(limit))
    if (before !== undefined) params.set('before', before)
    const query = params.toString()
    return request<readonly DailyOrder[]>(
      `/daily-orders/history${query ? `?${query}` : ''}`,
      { schema: DailyOrderListSchema },
    )
  },

  async markOrderPlayed(id, playedAt) {
    return request<DailyOrder | null>(
      `/daily-orders/${encodeURIComponent(id)}/played`,
      { method: 'POST', body: { playedAt }, schema: DailyOrderSchema, nullable: true },
    )
  },

  async deleteDailyOrder(id) {
    await request<null>(`/daily-orders/${encodeURIComponent(id)}`, { method: 'DELETE', schema: null })
  },

  async listEpisodes(opts) {
    const query = opts?.channel ? `?channel=${encodeURIComponent(opts.channel)}` : ''
    return request<readonly MockEpisode[]>(`/episodes${query}`, { schema: EpisodeListSchema })
  },

  async getEpisode(slug) {
    // 新方案下回應一次帶齊 segments[]（每行已 R2 簽章），不再二次請求 url 端點。
    // audioUrl 來自舊 audio_r2_key 簽章（向後相容）；segments 為新路徑，兩者並行回傳。
    const content = await request<z.infer<typeof EpisodeContentSchema>>(
      `/episodes/${encodeURIComponent(slug)}`,
      { schema: EpisodeContentSchema },
    )
    return toEpisode(content)
  },

  async getDeliveredEpisode(orderId) {
    // 這筆訂單還沒交付（送出後才剛觸發、生成還沒結）→ 回 null，
    // 由前端 PlayerRoute fallback 到 listEpisodes()[0]。
    const content = await request<z.infer<typeof EpisodeContentSchema> | null>(
      `/daily-orders/${encodeURIComponent(orderId)}/episode`,
      { schema: EpisodeContentSchema, nullable: true },
    )
    if (content === null) return null
    return toEpisode(content)
  },

  async recordEpisodePlay(episodeId) {
    await request<null>(`/episodes/${encodeURIComponent(episodeId)}/play`, { method: 'POST', schema: null })
  },

  async triggerGenerateJob(orderId) {
    // 送訂單後 fire-and-forget 觸發 worker 跑生成 pipeline。
    // 後端回 202 + envelope；前端 Promise<void> 不解析 body。
    // 失敗由呼叫端 catch（DailyOrderProvider 僅 console.warn，不打斷 createOrder）。
    await request<null>(
      `/jobs/orders/${encodeURIComponent(orderId)}/generate`,
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

  async getAdminEpisodeStats() {
    return request<AdminEpisodeStatsResponse>('/admin/episodes', {
      schema: AdminEpisodeStatsResponseSchema,
      extraHeaders: adminHeaders(),
    })
  },

  async getAdminEpisodeGeneration(episodeId: string) {
    return request<AdminEpisodeGeneration>(
      `/admin/episodes/${encodeURIComponent(episodeId)}/generation`,
      { schema: AdminEpisodeGenerationSchema, extraHeaders: adminHeaders() },
    )
  },

  async listAdminChannels() {
    return request<readonly Channel[]>('/admin/channels', {
      schema: ChannelListSchema,
      extraHeaders: adminHeaders(),
    })
  },

  async createAdminChannel(input: CreateChannelInput) {
    const body = input satisfies components['schemas']['CreateChannelBody']
    return request<Channel>('/admin/channels', {
      method: 'POST',
      body,
      schema: ChannelSchema,
      extraHeaders: adminHeaders(),
    })
  },

  async updateAdminChannel(channelId: string, patch: UpdateChannelInput) {
    const body = patch satisfies components['schemas']['UpdateChannelBody']
    return request<Channel>(`/admin/channels/${encodeURIComponent(channelId)}`, {
      method: 'PATCH',
      body,
      schema: ChannelSchema,
      extraHeaders: adminHeaders(),
    })
  },

  async uploadAdminChannelCover(channelId: string, file: File) {
    // 後端收原始 body（省掉 python-multipart 依賴），Content-Type 就是檔案自己的 MIME。
    // 型別／大小的把關在後端做（magic bytes + 上限）——前端擋是體驗，不是安全邊界。
    return request<Channel>(`/admin/channels/${encodeURIComponent(channelId)}/cover`, {
      method: 'POST',
      rawBody: { data: file, contentType: file.type },
      schema: ChannelSchema,
      extraHeaders: adminHeaders(),
    })
  },

  async planAdminChannel(channelId: string) {
    return request<ChannelPlanResponse>(`/admin/channels/${encodeURIComponent(channelId)}/plan`, {
      method: 'POST',
      schema: ChannelPlanResponseSchema,
      extraHeaders: adminHeaders(),
    })
  },

  async listAdminChannelTopics(channelId: string, status?: string) {
    const query = status ? `?status=${encodeURIComponent(status)}` : ''
    return request<readonly ChannelTopic[]>(
      `/admin/channels/${encodeURIComponent(channelId)}/topics${query}`,
      { schema: ChannelTopicListSchema, extraHeaders: adminHeaders() },
    )
  },

  async updateAdminChannelTopic(
    channelId: string,
    topicId: string,
    patch: { readonly status?: 'candidate' | 'rejected'; readonly canonicalTopic?: string },
  ) {
    const body = patch satisfies components['schemas']['UpdateChannelTopicBody']
    return request<ChannelTopic>(
      `/admin/channels/${encodeURIComponent(channelId)}/topics/${encodeURIComponent(topicId)}`,
      { method: 'PATCH', body, schema: ChannelTopicSchema, extraHeaders: adminHeaders() },
    )
  },

  // 使用者端公開頻道：JWT 認證，跟上面 Admin 那組 email 白名單分開。
  async listChannels() {
    return request<readonly ChannelPublic[]>('/channels', { schema: ChannelPublicListSchema })
  },

  async getChannel(slug: string) {
    return request<ChannelPublic>(`/channels/${encodeURIComponent(slug)}`, {
      schema: ChannelPublicSchema,
    })
  },

  async subscribeChannel(slug: string) {
    await request<null>(`/channels/${encodeURIComponent(slug)}/subscribe`, {
      method: 'POST',
      schema: null,
    })
  },

  async unsubscribeChannel(slug: string) {
    await request<null>(`/channels/${encodeURIComponent(slug)}/subscribe`, {
      method: 'DELETE',
      schema: null,
    })
  },

  async listMySubscriptions() {
    return request<readonly ChannelPublic[]>('/channels/subscriptions', {
      schema: ChannelPublicListSchema,
    })
  },

  async getRecommendedEpisodes() {
    return request<readonly RecommendedEpisode[]>('/episodes/recommended', {
      schema: RecommendedEpisodeListSchema,
    })
  },

  async subscribePush(subscription: PushSubscriptionInput) {
    // body 型別靠 satisfies 釘住後端契約；endpoint 由瀏覽器提供，不做前端加工。
    const body = subscription satisfies components['schemas']['PushSubscribeBody']
    await request<null>('/notifications/subscription', {
      method: 'POST',
      body,
      schema: null,
    })
  },

  async unsubscribePush(endpoint: string) {
    const body = { endpoint } satisfies components['schemas']['PushUnsubscribeBody']
    await request<null>('/notifications/subscription', {
      method: 'DELETE',
      body,
      schema: null,
    })
  },
}
