import { createContextHook } from './createContextHook'
import { DailyOrderContext, type DailyOrderContextValue } from './dailyOrderContextValue'

export const useDailyOrder: () => DailyOrderContextValue = createContextHook(
  DailyOrderContext,
  'useDailyOrder',
  'DailyOrderProvider',
)
