import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { motion } from 'framer-motion'
import { RadioTower } from 'lucide-react'
import { api, AppError, type ChannelPublic } from '../api'
import type { MockEpisode } from '../lib'
import { useChannelSubscriptions } from '../state'
import { useSprings } from '../lib/motion'
import { EmptyState } from '../components/primitives/EmptyState'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { ChannelCover } from '../components/shared/ChannelCover'
import { EpisodeRow } from '../components/shared/EpisodeRow'

/** 頻道詳情頁：頻道資訊 + 大顆追蹤鈕 + 該頻道底下全部集數。 */
export function ChannelDetailRoute() {
  const { slug } = useParams<{ slug: string }>()
  const [channel, setChannel] = useState<ChannelPublic | null>(null)
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])
  const [error, setError] = useState<string | null>(null)
  const { has, toggle } = useChannelSubscriptions()
  const springs = useSprings()

  const load = useCallback(async (currentSlug: string): Promise<void> => {
    setError(null)
    setChannel(null)
    try {
      const [c, eps] = await Promise.all([
        api.getChannel(currentSlug),
        api.listEpisodes({ channel: currentSlug }),
      ])
      setChannel(c)
      setEpisodes(eps)
    } catch (err) {
      setError(err instanceof AppError ? err.message : '找不到這個頻道')
    }
  }, [])

  // 記上次抓過的 slug：擋 StrictMode 重複呼叫，同時保留「slug 真的換了要重抓」的行為
  // （同一個路由元件在 /channels/:slug 之間切換不會重新掛載，光靠 mounted 布林值不夠）。
  const lastSlugRef = useRef<string | null>(null)
  useEffect(() => {
    if (!slug || lastSlugRef.current === slug) return
    lastSlugRef.current = slug
    void load(slug)
  }, [slug, load])

  if (error !== null) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-6">
        <ErrorBanner message={error} />
      </div>
    )
  }

  if (channel === null) return null

  const following = has(channel.slug)

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      <div className="flex gap-4 items-center mb-6">
        <ChannelCover url={channel.coverImageUrl} slug={channel.slug} topic={channel.topic} size="xl" />
        <div className="min-w-0 flex-1">
          <h1 className="text-lg font-semibold text-text-primary">{channel.name}</h1>
          {channel.description && (
            <p className="text-sm text-text-secondary mt-1">{channel.description}</p>
          )}
          <p className="text-xs text-text-tertiary mt-1">{channel.episodeCount} 集</p>
          <motion.button
            type="button"
            onClick={() => void toggle(channel)}
            whileTap={springs.reduce ? undefined : { scale: 0.94 }}
            transition={springs.press}
            aria-pressed={following}
            className={`mt-3 px-4 h-9 rounded-full text-sm font-semibold border transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              following
                ? 'bg-accent/10 text-accent border-accent/30'
                : 'bg-accent text-white border-transparent'
            }`}
          >
            {following ? '已追蹤' : '追蹤'}
          </motion.button>
        </div>
      </div>

      {episodes.length === 0 ? (
        <EmptyState icon={RadioTower} title="這個頻道還沒有集數" size="compact" />
      ) : (
        <div className="space-y-2">
          {episodes.map(ep => (
            <EpisodeRow key={ep.id} ep={ep} variant="card" />
          ))}
        </div>
      )}
    </div>
  )
}
