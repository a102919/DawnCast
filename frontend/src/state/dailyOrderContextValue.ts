import { createContext } from 'react'
import type { DailyOrder, DailyOrderInput } from '../api'

export type DailyOrderContextValue = {
  /** 目前進行中（pending/queued）的訂單；null＝插槽是空的，可以點下一餐。 */
  readonly activeOrder: DailyOrder | null
  /** 已播放完成的訂單，倒序（最新在前）。 */
  readonly history: readonly DailyOrder[]
  /** 建立新訂單並立即觸發生成；已有進行中訂單時 reject（後端 409）。 */
  createOrder(input: DailyOrderInput): Promise<DailyOrder>
  /** 取消一筆 pending 訂單；queued 已開始生成時 reject（後端 409）。 */
  cancelOrder(id: string): Promise<void>
  markPlayed(id: string): Promise<DailyOrder | null>
  /** 往後翻一頁歷史紀錄；已到底時是 no-op。 */
  loadMoreHistory(): Promise<void>
  /** 重新拉 active + 第一頁 history；polling 命中時呼叫。 */
  refresh: () => Promise<void>
}

export const DailyOrderContext = createContext<DailyOrderContextValue | null>(null)
