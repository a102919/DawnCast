// 與 DailyOrder 相關的日期 / 時間純函式。
// 放在 lib/ 是因為這層只有 pure function、沒有 React 依賴,與既有 lib/format.ts、lib/time.ts 同層級。

import type { DailyOrder } from '../api'

/** 取本地時區的 YYYY-MM-DD。en-CA locale 剛好就是 ISO 順序。 */
export function toIsoDate(d: Date): string {
  return d.toLocaleDateString('en-CA')
}

/** 訂單是否鎖定：隨時點餐下 Sheet 每次打開都是建新單，沒有「送出前還能編輯」
 *  的窗口——只要不是 played（還在生成中或已在收聽），就不能再變動內容。 */
export function isOrderLocked(order: DailyOrder): boolean {
  return order.status !== 'played'
}
