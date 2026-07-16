import { createContext } from 'react'
import type { DailyOrder, DailyOrderInput } from '../api'

export type DailyOrderContextValue = {
  readonly todayDate: string
  readonly orders: ReadonlyMap<string, DailyOrder>
  getOrder(date: string): DailyOrder | null
  setOrder(date: string, input: DailyOrderInput): Promise<DailyOrder>
  deleteOrder(date: string): Promise<void>
  markPlayed(date: string): Promise<DailyOrder | null>
}

export const DailyOrderContext = createContext<DailyOrderContextValue | null>(null)