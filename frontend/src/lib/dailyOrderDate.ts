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

export function isToday(iso: string, now: Date = new Date()): boolean {
  return iso === toIsoDate(now)
}

// ─── 預設值與常數 ─────────────────────────────────────────────────────────

export const DEFAULT_DELIVERY_TIME = '07:00'

// ─── 訂單鎖定判斷 ─────────────────────────────────────────────────────────

/** 訂單是否鎖定：送出後（status 離開 pending）立即開始生成，不可再編輯。 */
export function isOrderLocked(order: DailyOrder): boolean {
  return order.status !== 'pending'
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