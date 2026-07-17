import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, Heart, Play, RotateCcw } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatDateZhTW } from './episodeData'
import type { MockEpisode } from './episodeData'
import { api } from '../api'
import { useFavorites } from '../state'

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
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <AlertCircle size={32} className="text-danger" />
          <p className="text-danger text-sm">{fetchError}</p>
          <button
            className="flex items-center gap-1.5 px-4 py-2 text-sm text-text-secondary bg-bg-secondary hover:bg-border rounded-md transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            onClick={() => setRetryKey(k => k + 1)}
          >
            <RotateCcw size={14} />
            重新載入
          </button>
        </div>
      ) : list.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center gap-3">
          <div className="w-12 h-12 rounded-full bg-bg-secondary flex items-center justify-center text-text-tertiary">
            <Heart size={22} />
          </div>
          <div className="text-text-secondary text-sm">
            <p className="font-medium text-text-primary mb-1">收藏清單是空的</p>
            <p>在首頁點擊愛心即可收藏喜歡的 podcast</p>
          </div>
          <Link
            to="/"
            className="mt-2 text-xs text-accent hover:underline"
          >
            前往首頁 →
          </Link>
        </div>
      ) : (
        <div className="space-y-2">
          {list.map(ep => (
            <Link key={ep.id} to={`/player/${ep.id}`}>
              <div className="relative p-4 rounded-lg border border-border bg-bg-primary hover:border-accent/40 hover:shadow-sm transition-all duration-fast">
                <div className="flex items-start justify-between gap-3 pr-16">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <span className="text-xs text-text-tertiary">E{ep.episode}</span>
                      <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
                        {ep.cefrLevel}
                      </span>
                      <span className="text-xs text-text-tertiary">{TOPIC_LABELS[ep.topic]}</span>
                    </div>
                    <div className="font-medium text-text-primary text-sm leading-snug">{ep.title}</div>
                    <div className="text-xs text-text-secondary mt-0.5">{ep.titleZh}</div>
                    <div className="text-xs text-text-tertiary mt-2">{formatDateZhTW(ep.publishedAt)}</div>
                  </div>
                </div>
                <div className="absolute bottom-3 right-3 w-7 h-7 rounded-full flex items-center justify-center bg-accent/10 text-accent">
                  <Play size={12} fill="currentColor" />
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}