import { createContextHook } from './createContextHook'
import { ActivityContext, type ActivityContextValue } from './activityContextValue'

export const useActivity: () => ActivityContextValue = createContextHook(
  ActivityContext,
  'useActivity',
  'ActivityProvider',
)
