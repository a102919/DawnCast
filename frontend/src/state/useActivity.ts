import { useContext } from 'react'
import { ActivityContext, type ActivityContextValue } from './activityContextValue'

export function useActivity(): ActivityContextValue {
  const ctx = useContext(ActivityContext)
  if (!ctx) throw new Error('useActivity must be used inside ActivityProvider')
  return ctx
}
