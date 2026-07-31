import { createContext } from 'react'
import type { DailyOrder, DailyOrderInput } from '../api'
import type { Episode } from '../types/episode'

/** 訂單→交付集數的解析結果。failed 不自動重試（防無限請求迴圈），
 *  只在使用者顯式 refresh() 時清除重來。 */
export type OrderEpisodeEntry =
  | { readonly state: 'loading' }
  | { readonly state: 'failed' }
  | { readonly state: 'done'; readonly episode: Episode | null }

export type DailyOrderContextValue = {
  /** 目前進行中（pending/queued）的訂單；null＝插槽是空的，可以點下一餐。 */
  readonly activeOrder: DailyOrder | null
  /** 已完成（ready/played/expired）的訂單，倒序（最新在前）。 */
  readonly history: readonly DailyOrder[]
  /** history 已翻到底，「載入更多」該收起來。 */
  readonly historyExhausted: boolean
  /** 最近一次載入／輪詢的使用者可讀訊息；null＝一切正常。 */
  readonly error: string | null
  /** 訂單 id → 交付集數解析快取（全 app 唯一一份，取代各頁面自建的 Map）。 */
  readonly orderEpisodes: ReadonlyMap<string, OrderEpisodeEntry>
  /** 解析某訂單的交付集數：快取直回、in-flight 去重、failed 不自動重試。 */
  resolveOrderEpisode(orderId: string): Promise<Episode | null>
  /** 建立新訂單並立即觸發生成；已有進行中訂單時 reject（後端 409）。 */
  createOrder(input: DailyOrderInput): Promise<DailyOrder>
  /** 取消一筆 pending 訂單；queued 已開始生成時 reject（後端 409）。 */
  cancelOrder(id: string): Promise<void>
  markPlayed(id: string): Promise<DailyOrder | null>
  /** 往後翻一頁歷史紀錄；已到底時是 no-op。 */
  loadMoreHistory(): Promise<void>
  /** 重新拉 active + 第一頁 history，並清除 failed 解析快取（顯式重試入口）。 */
  refresh: () => Promise<void>
}

export const DailyOrderContext = createContext<DailyOrderContextValue | null>(null)
