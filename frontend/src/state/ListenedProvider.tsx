import { useCallback, type ReactNode } from 'react'
import { ListenedContext, type ListenedContextValue } from './listenedContextValue'
import { useActivity } from './useActivity'

/** 已聽集數與 streak 現由 ActivityProvider 統一管理（上雲 + localStorage cache）；
 *  此 Provider 只是薄殼，維持既有 ListenedContextValue 介面給既有呼叫端（HomeRoute /
 *  EpisodeCard / ProgressRoute / PlayerRoute）不必改動。 */
export function ListenedProvider({ children }: { readonly children: ReactNode }) {
  const { listenedEpisodeIds, markListened } = useActivity()

  const markAsListened = useCallback((id: string) => {
    markListened(id)
  }, [markListened])

  const value: ListenedContextValue = { listenedIds: listenedEpisodeIds, markAsListened }

  return (
    <ListenedContext.Provider value={value}>
      {children}
    </ListenedContext.Provider>
  )
}
