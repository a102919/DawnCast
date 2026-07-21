import { useEffect, useMemo, useState } from 'react'
import { Heart } from 'lucide-react'
import type { MockEpisode } from './episodeData'
import { api } from '../api'
import { useFavorites } from '../state'
import { EmptyState } from '../components/primitives/EmptyState'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { EpisodeRow } from '../components/shared/EpisodeRow'

export function FavoritesRoute() {
  const { favorites } = useFavorites()
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)

  useEffect(() => {
    const load = async () => {
      setFetchError(null)
      try {
        const list = await api.listEpisodes()
        setEpisodes(list)
      } catch {
        setFetchError('節目資料載入失敗，請重試')
      }
    }
    void load()
  }, [retryKey])

  const list = useMemo<readonly MockEpisode[]>(() => {
    return episodes.filter(ep => favorites.has(ep.id))
  }, [episodes, favorites])

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-text-primary">收藏</h1>
        <p className="text-sm text-text-secondary mt-0.5">
          共 {list.length} 集 podcast
        </p>
      </div>

      {fetchError !== null ? (
        <ErrorBanner message={fetchError} onRetry={() => setRetryKey(k => k + 1)} retryLabel="重新載入" />
      ) : list.length === 0 ? (
        <EmptyState
          icon={Heart}
          title="收藏清單是空的"
          description="在首頁點擊愛心即可收藏喜歡的 podcast"
          action={{ label: '前往首頁', to: '/', variant: 'link' }}
        />
      ) : (
        <div className="space-y-2">
          {list.map(ep => (
            <EpisodeRow key={ep.id} ep={ep} variant="compact" />
          ))}
        </div>
      )}
    </div>
  )
}
