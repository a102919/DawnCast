import { z } from 'zod'
import type { AccountInfo, Activity, ActivityPatch, AdminEpisodeStatsResponse, Api, Channel, ChannelPlanResponse, ChannelPublic, ChannelTopic, DailyOrder, DictEntry, RecommendedEpisode, Settings, VocabItem } from './types'
import type { Episode } from '../types/episode'
import { CueSchema, SegmentSchema, SourceReferenceSchema } from './httpApi'
import type { MockEpisode } from '../lib/episode'
import { storageGet, storageSet } from '../lib/storage'

// mock 模式的集數列表 seed（runtime 僅此處使用；畫面真資料一律走 httpApi）。
// 擴充至 10 集以讓首頁 Hero / Weekly carousel 視覺成立；多元主題/CEFR 偶爾 isFeatured。
const SEED_EPISODES: readonly MockEpisode[] = [
  {
    id: 'episode_test_seed_1',
    title: 'Test Seed Episode',
    titleZh: '測試用 seed 集數',
    topic: 'tech',
    cefrLevel: 'B1',
    isFeatured: true,
    episode: 1,
    publishedAt: '2026-07-01',
    coverIcon: 'cpu',
  },
  {
    id: 'episode_test_seed_2',
    title: 'AI Agents in 2026',
    titleZh: '2026 年的 AI 代理',
    topic: 'tech',
    cefrLevel: 'B2',
    episode: 2,
    publishedAt: '2026-07-05',
    coverIcon: 'bot',
  },
  {
    id: 'episode_test_seed_3',
    title: 'Market Reads',
    titleZh: '市場解讀',
    topic: 'business',
    cefrLevel: 'B1',
    episode: 3,
    publishedAt: '2026-07-08',
    coverIcon: 'trending-up',
  },
  {
    id: 'episode_test_seed_4',
    title: 'Startup Funding Cycle',
    titleZh: '新創募資循環',
    topic: 'business',
    cefrLevel: 'A2',
    isFeatured: true,
    episode: 4,
    publishedAt: '2026-07-10',
    coverIcon: 'briefcase',
  },
  {
    id: 'episode_test_seed_5',
    title: 'Street Photography',
    titleZh: '街頭攝影',
    topic: 'culture',
    cefrLevel: 'B1',
    episode: 5,
    publishedAt: '2026-07-12',
    coverIcon: 'camera',
  },
  {
    id: 'episode_test_seed_6',
    title: 'Modern Art Movements',
    titleZh: '現代藝術運動',
    topic: 'culture',
    cefrLevel: 'B2',
    episode: 6,
    publishedAt: '2026-07-15',
    coverIcon: 'palette',
  },
  {
    id: 'episode_test_seed_7',
    title: 'Quantum Computing Basics',
    titleZh: '量子運算入門',
    topic: 'science',
    cefrLevel: 'B2',
    episode: 7,
    publishedAt: '2026-07-18',
    coverIcon: 'atom',
  },
  {
    id: 'episode_test_seed_8',
    title: 'Climate Models Explained',
    titleZh: '氣候模型解析',
    topic: 'science',
    cefrLevel: 'B1',
    isFeatured: true,
    episode: 8,
    publishedAt: '2026-07-20',
    coverIcon: 'globe',
  },
  {
    id: 'episode_test_seed_9',
    title: 'Why Open Source Wins',
    titleZh: '為什麼開源會贏',
    topic: 'tech',
    cefrLevel: 'B1',
    episode: 9,
    publishedAt: '2026-07-22',
    coverIcon: 'code',
  },
  {
    id: 'episode_test_seed_10',
    title: 'Daily Listening Habit',
    titleZh: '每日收聽習慣',
    topic: 'science',
    cefrLevel: 'A2',
    episode: 10,
    publishedAt: '2026-07-24',
    coverIcon: 'headphones',
  },
] as const

// mock 模式的頻道 seed：借用 SEED_EPISODES 既有的 topic 分類當作頻道對應（每個頻道對應
// 一個 topic），不用另外建一套獨立的頻道-集數關聯資料——mock 資料只需要撐起每種 UI 狀態。
const SEED_CHANNELS: readonly ChannelPublic[] = [
  { slug: 'tech-daily', name: '科技脈動', description: '每天一則科技新知', topic: 'tech', episodeCount: 3 },
  { slug: 'biz-weekly', name: '商業趨勢', description: '每週商業趨勢觀察', topic: 'business', episodeCount: 2 },
  { slug: 'culture-corner', name: '文化角落', description: '藝術與文化的日常', topic: 'culture', episodeCount: 2 },
  { slug: 'science-lab', name: '科學實驗室', description: '科學新知輕鬆懂', topic: 'science', episodeCount: 3 },
] as const

// mock fixture（public/data/episode.json）是手寫的單一示範檔，只需要滿足前端 domain
// Episode 型別（id/title/audioUrl/cues/segments/references）；後端真實 wire schema
// （httpApi.ts 的 EpisodeContentSchema）多出的 topic/cefrLevel/isFree 是驗「後端有沒有送」
// 用的，跟這份 demo fixture 是兩件事，故用獨立、範圍對齊 domain 型別的 schema，
// 不共用同一份會逼 fixture 硬塞不相關欄位。
// fixture 欄位漂移（見 lessons.md 2026-07-19 videoUrl→audioUrl 教訓）在這裡一樣會直接炸，
// 不會靜默播出無聲音檔。references optional：fixture 沒寫就當作「無來源」，UI 不渲染。
// audioUrl 改 nullable：新方案下整集 mp3 不再生產，fixture 留空字串 fallback 給極舊
// client（new Audio() 路徑）；segments 預設空陣列。
const MockEpisodeContentSchema = z.object({
  id: z.string(),
  title: z.string(),
  audioUrl: z.string().nullable().default(null),
  segments: z.array(SegmentSchema).default([]),
  cues: z.array(CueSchema),
  references: z.array(SourceReferenceSchema).optional(),
}) satisfies z.ZodType<Episode>

async function fetchMockEpisode(): Promise<Episode> {
  const res = await fetch('/data/episode.json')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data: unknown = await res.json()
  return MockEpisodeContentSchema.parse(data)
}

const VOCAB_KEY = 'dawncast:vocab'
const SETTINGS_KEY = 'dawncast:settings'
const FAVORITES_KEY = 'dawncast:favorites'
const CHANNEL_SUBS_KEY = 'dawncast:channel-subs'
const DAILY_ORDERS_KEY = 'dawncast:dailyOrders'
const ACTIVITY_KEY = 'dawncast:mockActivity'

const DEFAULT_ACTIVITY: Activity = {
  streakDates: [],
  listenMinutes: {},
  lookupCount: {},
  listenedEpisodeIds: [],
  lastPlayedEpisodeId: null,
  lastPlayedPosition: null,
  lastPlayedAt: null,
}

const DEFAULT_SETTINGS: Settings = {
  popupEnabled: true,
  playbackRate: 1,
  theme: 'auto',
  preferredTopics: [],
  defaultDeliveryTime: '07:00',
  cefrLevel: 'B1',
} as const

function readVocab(): VocabItem[] {
  const parsed = storageGet<unknown>(VOCAB_KEY)
  return Array.isArray(parsed) ? (parsed as VocabItem[]) : []
}

function writeVocab(items: VocabItem[]): void {
  storageSet(VOCAB_KEY, items)
}

function readSettings(): Settings {
  return { ...DEFAULT_SETTINGS, ...(storageGet<Partial<Settings>>(SETTINGS_KEY) ?? {}) }
}

function writeSettings(s: Settings): void {
  storageSet(SETTINGS_KEY, s)
}

function readFavorites(): string[] {
  const parsed = storageGet<unknown[]>(FAVORITES_KEY)
  return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
}

function writeFavorites(ids: readonly string[]): void {
  storageSet(FAVORITES_KEY, [...ids])
}

function readChannelSubs(): string[] {
  const parsed = storageGet<unknown[]>(CHANNEL_SUBS_KEY)
  return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : []
}

function writeChannelSubs(slugs: readonly string[]): void {
  storageSet(CHANNEL_SUBS_KEY, [...slugs])
}

/** 隨時點餐：單一陣列存全部訂單（歷史上允許同一天多筆），沒有分表查詢能力，
 *  直接在記憶體裡 filter/sort，鏡像後端 partial unique index 的「同一時間僅一筆
 *  進行中」語意由 createDailyOrder 自己檢查。 */
function readDailyOrders(): DailyOrder[] {
  const parsed = storageGet<unknown[]>(DAILY_ORDERS_KEY)
  return Array.isArray(parsed) ? (parsed as DailyOrder[]) : []
}

function writeDailyOrders(orders: readonly DailyOrder[]): void {
  storageSet(DAILY_ORDERS_KEY, [...orders])
}

function readActivity(): Activity {
  return { ...DEFAULT_ACTIVITY, ...(storageGet<Partial<Activity>>(ACTIVITY_KEY) ?? {}) }
}

function writeActivity(a: Activity): void {
  storageSet(ACTIVITY_KEY, a)
}

// 簡化版合併（不要求跟後端逐 bit 一致，純粹讓 mock 模式功能可用）：
// streak/listened id 去重、counter 遞增、lastPlayed 直接覆蓋（mock 單裝置無亂序問題）。
function mergeActivity(current: Activity, patch: ActivityPatch): Activity {
  const streakDates = patch.addStreakDate
    ? [...new Set([...current.streakDates, patch.addStreakDate])]
    : current.streakDates
  const listenedEpisodeIds = patch.addListenedEpisodeId
    ? [...new Set([...current.listenedEpisodeIds, patch.addListenedEpisodeId])]
    : current.listenedEpisodeIds
  const listenMinutes = patch.addListenMinutes
    ? {
        ...current.listenMinutes,
        [patch.addListenMinutes.month]:
          (current.listenMinutes[patch.addListenMinutes.month] ?? 0) + patch.addListenMinutes.minutes,
      }
    : current.listenMinutes
  const lookupCount = patch.addLookupCount
    ? {
        ...current.lookupCount,
        [patch.addLookupCount.month]:
          (current.lookupCount[patch.addLookupCount.month] ?? 0) + patch.addLookupCount.count,
      }
    : current.lookupCount
  const lastPlayed = patch.lastPlayed
    ? {
        lastPlayedEpisodeId: patch.lastPlayed.episodeId,
        lastPlayedPosition: patch.lastPlayed.position,
        lastPlayedAt: patch.lastPlayed.at,
      }
    : {
        lastPlayedEpisodeId: current.lastPlayedEpisodeId,
        lastPlayedPosition: current.lastPlayedPosition,
        lastPlayedAt: current.lastPlayedAt,
      }
  return { streakDates, listenedEpisodeIds, listenMinutes, lookupCount, ...lastPlayed }
}

// dict.json 懶載入
let dictCache: Record<string, DictEntry> | null = null

async function loadDict(): Promise<Record<string, DictEntry>> {
  if (dictCache) return dictCache
  const res = await fetch('/data/dict.json')
  const raw: unknown = await res.json()
  dictCache = raw as Record<string, DictEntry>
  return dictCache
}

export const mockApi: Api = {
  async lookupDict(word) {
    const dict = await loadDict()
    const key = word.toLowerCase()
    return dict[key] ?? null
  },

  async addVocab(item) {
    const items = readVocab()
    // 去重鍵須與後端 / DB unique 對齊（lemma + sourceEpisodeId + sourceLineNo），
    // 否則同字在不同集但同行號會被誤判重複。
    const existing = items.find(
      v =>
        v.lemma === item.lemma &&
        v.sourceEpisodeId === item.sourceEpisodeId &&
        v.sourceLineNo === item.sourceLineNo,
    )
    if (existing) return existing
    const newItem: VocabItem = {
      ...item,
      id: crypto.randomUUID(),
      createdAt: new Date().toISOString(),
      nextReview: new Date().toLocaleDateString('en-CA'),
      interval: 1,
      ease: 2.5,
      status: 1,
    }
    writeVocab([newItem, ...items])
    return newItem
  },

  async removeVocab(id) {
    const items = readVocab()
    writeVocab(items.filter(v => v.id !== id))
  },

  async listVocab() {
    return readVocab()
  },

  async searchVocab(query) {
    const items = readVocab()
    const q = query.toLowerCase()
    return items.filter(
      v => v.word.toLowerCase().includes(q) || v.translation.includes(q)
    )
  },

  async getSettings() {
    return readSettings()
  },

  async updateSettings(patch) {
    const current = readSettings()
    const updated: Settings = { ...current, ...patch }
    writeSettings(updated)
    return updated
  },

  async clearVocab() {
    writeVocab([])
  },

  async getFavorites() {
    return readFavorites()
  },

  async addFavorite(id) {
    const list = readFavorites()
    if (list.includes(id)) return
    writeFavorites([id, ...list])
  },

  async removeFavorite(id) {
    const list = readFavorites()
    writeFavorites(list.filter(x => x !== id))
  },

  async isFavorite(id) {
    return readFavorites().includes(id)
  },

  async getActiveOrder() {
    const orders = readDailyOrders()
    return orders.find(o => o.status === 'pending' || o.status === 'queued') ?? null
  },

  async createDailyOrder(input) {
    const orders = readDailyOrders()
    if (orders.some(o => o.status === 'pending' || o.status === 'queued')) {
      throw new Error('尚有訂單處理中，請等目前訂單完成後再點新的')
    }
    const now = new Date().toISOString()
    const order: DailyOrder = {
      id: crypto.randomUUID(),
      date: now.slice(0, 10),
      selectedTopics: [...input.selectedTopics],
      ...(input.specificRequest !== undefined && input.specificRequest !== ''
        ? { specificRequest: input.specificRequest }
        : {}),
      status: 'pending',
      deliveryTime: '07:00',
      createdAt: now,
      updatedAt: now,
      entryMode: input.entryMode ?? 'topic',
      lengthTier: input.lengthTier ?? 'medium',
    }
    writeDailyOrders([...orders, order])
    return order
  },

  async listOrderHistory(limit, before) {
    const played = readDailyOrders()
      .filter(o => o.status === 'ready' || o.status === 'played')
      .filter(o => before === undefined || o.createdAt < before)
      .sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
    return played.slice(0, limit ?? 20)
  },

  async markOrderPlayed(id, playedAt) {
    const orders = readDailyOrders()
    const current = orders.find(o => o.id === id)
    if (!current) return null
    const updated: DailyOrder = { ...current, status: 'played', playedAt, updatedAt: playedAt }
    writeDailyOrders(orders.map(o => o.id === id ? updated : o))
    return updated
  },

  async deleteDailyOrder(id) {
    const orders = readDailyOrders()
    const current = orders.find(o => o.id === id)
    if (!current) throw new Error('查無此訂單')
    if (current.status !== 'pending') throw new Error('訂單已開始生成，無法取消')
    writeDailyOrders(orders.filter(o => o.id !== id))
  },

  async updateVocab(id, patch) {
    const items = readVocab()
    writeVocab(items.map(v => v.id === id ? { ...v, ...patch } : v))
  },

  async listEpisodes(opts) {
    if (!opts?.channel) return SEED_EPISODES
    const chan = SEED_CHANNELS.find(c => c.slug === opts.channel)
    if (!chan) return []
    return SEED_EPISODES.filter(ep => ep.topic === chan.topic)
  },

  // mock 模式只有單一示範節目檔，無論 slug 一律回 /data/episode.json，
  // 與 Phase 4a 前的既有行為（HomeRoute/PlayerRoute 直接 fetch 此檔）完全一致。
  async getEpisode(_slug) {
    return fetchMockEpisode()
  },

  // mock 模式：以 orderId 字串做簡單 hash 對 SEED_EPISODES 取模，決定性回該集，
  // 讓任一訂單都能測到 hero 正常路徑；null 路徑由測試自己 mock。
  async getDeliveredEpisode(orderId) {
    const seed = SEED_EPISODES
    if (seed.length === 0) return null
    let hash = 0
    for (let i = 0; i < orderId.length; i++) hash = (hash * 31 + orderId.charCodeAt(i)) | 0
    const idx = Math.abs(hash) % seed.length
    const target = seed[idx]
    if (!target) return null
    const mock = await fetchMockEpisode()
    return { ...mock, id: target.id, title: target.title }
  },

  // mock 模式沒有 episodes.play_count 可寫，播放頁背景呼叫此處必須是 noop——
  // 丟錯會讓 mock 模式的播放器炸掉，這裡不是 admin 唯讀查詢，是使用者端播放路徑。
  async recordEpisodePlay(_episodeId) {
    return undefined
  },

  // mock 模式沒有真 worker，createOrder 仍會呼叫此處但純 noop
  async triggerGenerateJob(_orderId) {
    return undefined
  },

  async getActivity() {
    return readActivity()
  },

  async patchActivity(patch) {
    const updated = mergeActivity(readActivity(), patch)
    writeActivity(updated)
    return updated
  },

  // T4 帳號自我管理 — mock 模式：回預設 AccountInfo。
  // 真實應用不會走這條（http 模式才會接 /me）；保留 mock 讓 demo 模式也能呼叫。
  async getMe() {
    const info: AccountInfo = {
      id: 'mock-user',
      email: 'mock@example.com',
      tz: 'Asia/Taipei',
      deliveryTime: '07:00',
      createdAt: new Date().toISOString(),
    }
    return info
  },

  // mock 模式刪除：模擬 backend cascade 行為，清掉所有 dawncast: 開頭的 localStorage keys。
  // handler 端會再呼叫 supabase.auth.signOut() + localStorage.clear()，這裡只負責 mock API contract。
  async deleteAccount() {
    const keysToRemove: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith('dawncast:')) keysToRemove.push(key)
    }
    for (const k of keysToRemove) localStorage.removeItem(k)
  },

  // mock 模式沒有真實 episodes/gen_metrics/user_activity 資料可查。
  async getAdminEpisodeStats(): Promise<AdminEpisodeStatsResponse> {
    throw new Error('mock 模式不支援管理員查詢，請將 VITE_USE_MOCK 設為 false')
  },

  // 頻道管理同理：狀態在 DB，mock 沒有可寫的地方，一律明確擋掉而不是回假資料
  // 讓人誤以為建好了。
  async listAdminChannels(): Promise<readonly Channel[]> {
    throw new Error('mock 模式不支援頻道管理，請將 VITE_USE_MOCK 設為 false')
  },

  async createAdminChannel(): Promise<Channel> {
    throw new Error('mock 模式不支援頻道管理，請將 VITE_USE_MOCK 設為 false')
  },

  async updateAdminChannel(): Promise<Channel> {
    throw new Error('mock 模式不支援頻道管理，請將 VITE_USE_MOCK 設為 false')
  },

  async uploadAdminChannelCover(): Promise<Channel> {
    throw new Error('mock 模式不支援頻道管理，請將 VITE_USE_MOCK 設為 false')
  },

  async planAdminChannel(): Promise<ChannelPlanResponse> {
    throw new Error('mock 模式不支援頻道管理，請將 VITE_USE_MOCK 設為 false')
  },

  async listAdminChannelTopics(): Promise<readonly ChannelTopic[]> {
    throw new Error('mock 模式不支援頻道管理，請將 VITE_USE_MOCK 設為 false')
  },

  async updateAdminChannelTopic(): Promise<ChannelTopic> {
    throw new Error('mock 模式不支援頻道管理，請將 VITE_USE_MOCK 設為 false')
  },

  // 使用者端公開頻道：訂閱狀態比照 favorites 存 localStorage，讓 mock 模式也能完整走過
  // 追蹤→首頁推薦→取消追蹤整個流程。
  async listChannels() {
    return SEED_CHANNELS
  },

  async getChannel(slug) {
    const found = SEED_CHANNELS.find(c => c.slug === slug)
    if (!found) throw new Error(`mock 模式找不到頻道：${slug}`)
    return found
  },

  async subscribeChannel(slug) {
    const subs = readChannelSubs()
    if (!subs.includes(slug)) writeChannelSubs([slug, ...subs])
  },

  async unsubscribeChannel(slug) {
    writeChannelSubs(readChannelSubs().filter(s => s !== slug))
  },

  async listMySubscriptions() {
    const subs = readChannelSubs()
    return SEED_CHANNELS.filter(c => subs.includes(c.slug))
  },

  async getRecommendedEpisodes() {
    const subscribedTopics = new Set(
      SEED_CHANNELS.filter(c => readChannelSubs().includes(c.slug)).map(c => c.topic),
    )
    const listened = new Set(readActivity().listenedEpisodeIds)
    return SEED_EPISODES
      .filter(ep => subscribedTopics.has(ep.topic) && !listened.has(ep.id))
      .map((ep): RecommendedEpisode => {
        const chan = SEED_CHANNELS.find(c => c.topic === ep.topic)
        return { ...ep, channelSlug: chan?.slug ?? '', channelName: chan?.name ?? '' }
      })
  },

  // Push 訂閱：mock 模式沒有後端可登錄，noop 即可（設定頁的 toggle 仍能操作
  // 瀏覽器層的訂閱狀態，只是不會有人推送）。
  async subscribePush(): Promise<void> {},

  async unsubscribePush(): Promise<void> {},
}
