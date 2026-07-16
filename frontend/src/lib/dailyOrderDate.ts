// 與 DailyOrder 相關的日期 / 時間純函式。
// 放在 lib/ 是因為這層只有 pure function、沒有 React 依賴,與既有 lib/format.ts、lib/time.ts 同層級。

import type { DailyOrder } from '../api'

// ─── 日期字串（YYYY-MM-DD）───────────────────────────────────────────────

/** 取本地時區的 YYYY-MM-DD。en-CA locale 剛好就是 ISO 順序。 */
export function toIsoDate(d: Date): string {
  return d.toLocaleDateString('en-CA')
}

/** 把 YYYY-MM-DD 字串解析為本地時區的 Date（00:00）。 */
export function parseIsoDate(iso: string): Date {
  return new Date(iso + 'T00:00:00')
}

/** 在 YYYY-MM-DD 上加減天數,回新的 YYYY-MM-DD。n 可負。 */
export function addDays(iso: string, n: number): string {
  const d = parseIsoDate(iso)
  d.setDate(d.getDate() + n)
  return toIsoDate(d)
}

/** 兩 YYYY-MM-DD 的天數差（b - a）。回正/負整數。 */
export function diffDays(a: string, b: string): number {
  const ms = parseIsoDate(b).getTime() - parseIsoDate(a).getTime()
  return Math.round(ms / 86_400_000)
}

export function isPast(iso: string, now: Date = new Date()): boolean {
  return iso < toIsoDate(now)
}

export function isFuture(iso: string, now: Date = new Date()): boolean {
  return iso > toIsoDate(now)
}

export function isToday(iso: string, now: Date = new Date()): boolean {
  return iso === toIsoDate(now)
}

// ─── 出餐時間 'HH:MM' helpers ─────────────────────────────────────────────

const HHMM_RE = /^(\d{1,2}):(\d{2})$/

export function isValidHhmm(s: string): boolean {
  const m = HHMM_RE.exec(s)
  if (!m) return false
  const h = Number(m[1])
  const min = Number(m[2])
  return h >= 0 && h <= 23 && min >= 0 && min <= 59
}

/** 合併 YYYY-MM-DD + 'HH:MM' 為本地時區的 Date。 */
export function combineDateTime(iso: string, hhmm: string): Date {
  const m = HHMM_RE.exec(hhmm)
  if (!m) return parseIsoDate(iso)
  const d = parseIsoDate(iso)
  d.setHours(Number(m[1]), Number(m[2]), 0, 0)
  return d
}

/** 截止時間 = 出餐時間 - CUTOFF_HOURS_BEFORE_DELIVERY 小時。 */
export function cutoffTime(iso: string, hhmm: string): Date {
  const delivery = combineDateTime(iso, hhmm)
  delivery.setHours(delivery.getHours() - CUTOFF_HOURS_BEFORE_DELIVERY)
  return delivery
}

// ─── 預設值與常數 ─────────────────────────────────────────────────────────

export const CUTOFF_HOURS_BEFORE_DELIVERY = 6
export const DEFAULT_DELIVERY_TIME = '07:00'

export const DELIVERY_TIME_OPTIONS: readonly { readonly value: string; readonly label: string }[] = [
  { value: '06:00', label: '06:00 早起' },
  { value: '07:00', label: '07:00 通勤' },
  { value: '08:00', label: '08:00 早餐' },
  { value: '12:00', label: '12:00 午休' },
  { value: '18:00', label: '18:00 下班' },
  { value: '21:00', label: '21:00 睡前' },
] as const

/** 6 個出餐時段的字串值集合，給 isDeliveryTime 守衛用。 */
export const DELIVERY_TIME_VALUES: readonly string[] = DELIVERY_TIME_OPTIONS.map(o => o.value)

/** 是否為 6 個出餐時段之一（比 isValidHhmm 更嚴，限定只能從 chips 選）。 */
export function isDeliveryTime(s: string): boolean {
  return (DELIVERY_TIME_VALUES as readonly string[]).includes(s) && isValidHhmm(s)
}

// ─── 訂單鎖定判斷 ─────────────────────────────────────────────────────────

/**
 * 訂單是否鎖定：
 * - 已 played：永久鎖
 * - 現在時間 >= 出餐前 6 小時（截止時間）：鎖
 * - 其他：可編輯
 */
export function isOrderLocked(order: DailyOrder, now: Date = new Date()): boolean {
  if (order.status === 'played') return true
  return now.getTime() >= cutoffTime(order.date, order.deliveryTime).getTime()
}

/** 還剩多久鎖定（毫秒）。已鎖定回 0。負數代表已過截止。 */
export function msUntilLock(order: DailyOrder, now: Date = new Date()): number {
  if (order.status === 'played') return 0
  return cutoffTime(order.date, order.deliveryTime).getTime() - now.getTime()
}

/** 把毫秒轉成 "X 小時 Y 分鐘" / "Y 分鐘" 形式。負數回 "已過截止"。 */
export function formatCountdown(ms: number): string {
  if (ms <= 0) return '已過截止'
  const totalMin = Math.floor(ms / 60_000)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  if (h > 0) return `${h} 小時 ${m} 分鐘`
  return `${m} 分鐘`
}

// ─── 行事曆輔助 ───────────────────────────────────────────────────────────

/** 從 today 起往後 N 天（含 today）的日期陣列。 */
export function nextNDays(today: string, n: number): readonly string[] {
  const result: string[] = []
  for (let i = 0; i < n; i++) {
    result.push(addDays(today, i))
  }
  return result
}

/** 從 today 起往前 N 天（不含 today）的日期陣列，由近到遠。 */
export function previousNDays(today: string, n: number): readonly string[] {
  const result: string[] = []
  for (let i = 1; i <= n; i++) {
    result.push(addDays(today, -i))
  }
  return result
}

// ─── 星期顯示 ─────────────────────────────────────────────────────────────

export const WEEKDAY_LABELS: readonly string[] = ['日', '一', '二', '三', '四', '五', '六'] as const

/** YYYY-MM-DD 對應的星期幾字（一/二/.../日）。 */
export function getWeekdayLabel(iso: string): string {
  const idx = parseIsoDate(iso).getDay()
  return WEEKDAY_LABELS[idx] ?? ''
}