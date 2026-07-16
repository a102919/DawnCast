import type { AccountInfo, Activity, ActivityPatch, Api, DailyOrder, DictEntry, Settings, VocabItem } from './types'
import type { Episode } from '../types/episode'
import { EPISODES } from '../routes/episodeData'

const VOCAB_KEY = 'dawncast:vocab'
const SETTINGS_KEY = 'dawncast:settings'
const FAVORITES_KEY = 'dawncast:favorites'
const DAILY_ORDER_KEY_PREFIX = 'dawncast:dailyOrder:'
const LAST_ORDER_DATE_KEY = 'dawncast:lastOrderDate'
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
  popupDontShowAgain: false,
  playbackRate: 1,
  fontSize: 'md',
  theme: 'auto',
  preferredTopics: [],
  defaultDeliveryTime: '07:00',
} as const

function readVocab(): VocabItem[] {
  const raw = localStorage.getItem(VOCAB_KEY)
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed as VocabItem[]
  } catch {
    return []
  }
}

function writeVocab(items: VocabItem[]): void {
  localStorage.setItem(VOCAB_KEY, JSON.stringify(items))
}

function readSettings(): Settings {
  const raw = localStorage.getItem(SETTINGS_KEY)
  if (!raw) return { ...DEFAULT_SETTINGS }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return { ...DEFAULT_SETTINGS }
    return { ...DEFAULT_SETTINGS, ...(parsed as Partial<Settings>) }
  } catch {
    return { ...DEFAULT_SETTINGS }
  }
}

function writeSettings(s: Settings): void {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(s))
}

function readFavorites(): string[] {
  const raw = localStorage.getItem(FAVORITES_KEY)
  if (!raw) return []
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((x): x is string => typeof x === 'string')
  } catch {
    return []
  }
}

function writeFavorites(ids: readonly string[]): void {
  localStorage.setItem(FAVORITES_KEY, JSON.stringify([...ids]))
}

function readDailyOrder(date: string): DailyOrder | null {
  const raw = localStorage.getItem(DAILY_ORDER_KEY_PREFIX + date)
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    return parsed as DailyOrder
  } catch {
    return null
  }
}

function writeDailyOrder(order: DailyOrder): void {
  localStorage.setItem(DAILY_ORDER_KEY_PREFIX + order.date, JSON.stringify(order))
}

function removeDailyOrder(date: string): void {
  localStorage.removeItem(DAILY_ORDER_KEY_PREFIX + date)
}

/** 從 a 到 b（含）逐日枚舉 YYYY-MM-DD。要求 a <= b，否則回空陣列。 */
function enumerateDateRange(fromDate: string, toDate: string): readonly string[] {
  if (fromDate > toDate) return []
  const result: string[] = []
  const start = new Date(fromDate + 'T00:00:00')
  const end = new Date(toDate + 'T00:00:00')
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return []
  const cursor = new Date(start)
  while (cursor.getTime() <= end.getTime()) {
    const iso = cursor.toLocaleDateString('en-CA')
    result.push(iso)
    cursor.setDate(cursor.getDate() + 1)
  }
  return result
}

function readLastOrderDate(): string | null {
  return localStorage.getItem(LAST_ORDER_DATE_KEY)
}

function writeLastOrderDate(date: string): void {
  localStorage.setItem(LAST_ORDER_DATE_KEY, date)
}

function readActivity(): Activity {
  const raw = localStorage.getItem(ACTIVITY_KEY)
  if (!raw) return { ...DEFAULT_ACTIVITY }
  try {
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return { ...DEFAULT_ACTIVITY }
    return { ...DEFAULT_ACTIVITY, ...(parsed as Partial<Activity>) }
  } catch {
    return { ...DEFAULT_ACTIVITY }
  }
}

function writeActivity(a: Activity): void {
  localStorage.setItem(ACTIVITY_KEY, JSON.stringify(a))
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

  async resetPopupPreferences() {
    const current = readSettings()
    const updated: Settings = { ...current, popupEnabled: true, popupDontShowAgain: false }
    writeSettings(updated)
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

  async getDailyOrder(date) {
    return readDailyOrder(date)
  },

  async saveDailyOrder(order) {
    writeDailyOrder(order)
    return order
  },

  async listDailyOrders(fromDate, toDate) {
    const dates = enumerateDateRange(fromDate, toDate)
    const orders: DailyOrder[] = []
    for (const d of dates) {
      const o = readDailyOrder(d)
      if (o) orders.push(o)
    }
    return orders
  },

  async markOrderPlayed(date, playedAt) {
    const current = readDailyOrder(date)
    if (!current) return null
    const updated: DailyOrder = {
      ...current,
      status: 'played',
      playedAt,
      updatedAt: playedAt,
    }
    writeDailyOrder(updated)
    return updated
  },

  async deleteDailyOrder(date) {
    removeDailyOrder(date)
  },

  async getLastOrderDate() {
    return readLastOrderDate()
  },

  async setLastOrderDate(date) {
    writeLastOrderDate(date)
  },

  async updateVocab(id, patch) {
    const items = readVocab()
    writeVocab(items.map(v => v.id === id ? { ...v, ...patch } : v))
  },

  async listEpisodes() {
    return EPISODES
  },

  // mock 模式只有單一示範節目檔，無論 slug 一律回 /data/episode.json，
  // 與 Phase 4a 前的既有行為（HomeRoute/PlayerRoute 直接 fetch 此檔）完全一致。
  async getEpisode(_slug) {
    const res = await fetch('/data/episode.json')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: unknown = await res.json()
    return data as Episode
  },

  // mock 模式無 daily-orders 對應邏輯；直接回示範集（讓 PlayerRoute ?date= 連結在
  // mock 下也可走通）。null 路徑靠 mock 自行決定，這裡採非 null 簡化。
  async getDeliveredEpisode(_date) {
    const res = await fetch('/data/episode.json')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data: unknown = await res.json()
    return data as Episode
  },

  // T1：mock 模式沒有真 worker，setOrder 仍會呼叫此處但純 noop
  async triggerGenerateJob(_date) {
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
}
