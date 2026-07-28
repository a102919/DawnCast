import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../api'
import type { MockEpisode } from '../lib'
import { EpisodesContext, type EpisodesContextValue } from './episodesContextValue'

// 集中 api.listEpisodes() 的抓取：mount 時打一次，DailyRoute / ProgressRoute /
// FavoritesRoute / HomeRoute 共用同一份結果，避免切頁重複打相同 API。
export function EpisodesProvider({ children }: { readonly children: ReactNode }) {
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const list = await api.listEpisodes()
      setEpisodes(list)
    } catch {
      setError('節目資料載入失敗')
    } finally {
      setLoading(false)
    }
  }, [])

  // 初次掛載跑一次載入。mounted ref 確保 StrictMode 雙 mount 只觸發一次。
  const mountedRef = useRef<boolean>(false)
  useEffect(() => {
    if (mountedRef.current) return
    mountedRef.current = true
    void refresh()
  }, [refresh])

  const value: EpisodesContextValue = { episodes, loading, error, refresh }

  return (
    <EpisodesContext.Provider value={value}>
      {children}
    </EpisodesContext.Provider>
  )
}
