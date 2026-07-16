import { useContext } from 'react'
import { ListenedContext, type ListenedContextValue } from './listenedContextValue'

export function useListened(): ListenedContextValue {
  const ctx = useContext(ListenedContext)
  if (!ctx) throw new Error('useListened must be used inside ListenedProvider')
  return ctx
}
