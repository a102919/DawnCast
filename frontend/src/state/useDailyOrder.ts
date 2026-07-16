import { useContext } from 'react'
import { DailyOrderContext, type DailyOrderContextValue } from './dailyOrderContextValue'

export function useDailyOrder(): DailyOrderContextValue {
  const ctx = useContext(DailyOrderContext)
  if (!ctx) throw new Error('useDailyOrder must be used inside DailyOrderProvider')
  return ctx
}
