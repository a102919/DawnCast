import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { RadioTower } from 'lucide-react'
import { api, AppError, type ChannelPublic } from '../api'
import { useChannelSubscriptions } from '../state'
import { useSprings } from '../lib/motion'
import { EmptyState } from '../components/primitives/EmptyState'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { ChannelCover } from '../components/shared/ChannelCover'

/** 頻道探索頁：全部上架中的頻道，供瀏覽與訂閱。點卡片本體進詳情頁，
 *  追蹤鈕獨立吃事件（stopPropagation）不觸發導頁。 */
export function ChannelsRoute() {
  const [channels, setChannels] = useState<readonly ChannelPublic[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { has, toggle } = useChannelSubscriptions()
  const springs = useSprings()

  const load = useCallback(async (): Promise<void> => {
    setError(null)
    try {
      setChannels(await api.listChannels())
    } catch (err) {
      setError(err instanceof AppError ? err.message : '頻道載入失敗')
    }
  }, [])

  const mountedRef = useRef<boolean>(false)
  useEffect(() => {
    if (mountedRef.current) return
    mountedRef.current = true
    void load()
  }, [load])

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-text-primary">頻道</h1>
        <p className="text-sm text-text-secondary mt-0.5">追蹤頻道，在首頁收到最新集數推薦</p>
      </div>

      {error !== null ? (
        <ErrorBanner message={error} onRetry={load} retryLabel="重新載入" />
      ) : channels === null ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <ChannelCardSkeleton key={i} />
          ))}
        </div>
      ) : channels.length === 0 ? (
        <EmptyState icon={RadioTower} title="目前還沒有任何頻道" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <AnimatePresence mode="popLayout" initial={false}>
            {channels.map(channel => (
              <motion.div
                key={channel.slug}
                layout
                initial={{ opacity: 0, scale: 0.96 }}
                animate={{ opacity: 1, scale: 1, transition: springs.gentle }}
                exit={{ opacity: 0, scale: 0.96, transition: springs.snappy }}
              >
                <ChannelCard
                  channel={channel}
                  following={has(channel.slug)}
                  onToggle={() => void toggle(channel)}
                />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}

function ChannelCard({
  channel,
  following,
  onToggle,
}: {
  readonly channel: ChannelPublic
  readonly following: boolean
  readonly onToggle: () => void
}) {
  const springs = useSprings()

  return (
    <Link
      to={`/channels/${channel.slug}`}
      className="relative overflow-hidden block p-3 sm:p-4 rounded-2xl border border-border bg-bg-primary hover:border-accent/40 hover:shadow-md transition-[border-color,box-shadow] duration-fast"
    >
      <div className="absolute inset-x-0 top-0 h-10 bg-gradient-to-b from-white/[0.05] to-transparent pointer-events-none rounded-t-2xl" />
      <motion.div
        className="relative flex gap-3"
        whileTap={springs.reduce ? undefined : { scale: 0.98 }}
        transition={springs.press}
      >
        <ChannelCover url={channel.coverImageUrl} slug={channel.slug} topic={channel.topic} size="lg" />
        <div className="min-w-0 flex-1 flex flex-col">
          <div className="font-semibold text-text-primary text-sm truncate">{channel.name}</div>
          {channel.description && (
            <p className="text-xs text-text-secondary mt-0.5 line-clamp-2">{channel.description}</p>
          )}
          <div className="mt-auto pt-2 flex items-center justify-between gap-2">
            <span className="text-xs text-text-tertiary">{channel.episodeCount} 集</span>
            <motion.button
              type="button"
              onClick={e => {
                e.preventDefault()
                e.stopPropagation()
                onToggle()
              }}
              whileTap={springs.reduce ? undefined : { scale: 0.94 }}
              transition={springs.press}
              aria-label={following ? '取消追蹤' : '追蹤'}
              aria-pressed={following}
              className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                following
                  ? 'bg-accent/10 text-accent border-accent/30'
                  : 'bg-bg-secondary text-text-primary border-border'
              }`}
            >
              {following ? '已追蹤' : '追蹤'}
            </motion.button>
          </div>
        </div>
      </motion.div>
    </Link>
  )
}

function ChannelCardSkeleton() {
  return (
    <div className="relative overflow-hidden p-4 rounded-2xl border border-border bg-bg-primary">
      <div className="flex gap-3">
        <div className="motion-safe:animate-pulse w-24 h-24 rounded-2xl bg-bg-secondary shrink-0" />
        <div className="min-w-0 flex-1 flex flex-col gap-2">
          <div className="motion-safe:animate-pulse h-4 w-2/3 rounded bg-bg-secondary" />
          <div className="motion-safe:animate-pulse h-3 w-full rounded bg-bg-secondary" />
          <div className="mt-auto pt-2 flex items-center justify-between gap-2">
            <div className="motion-safe:animate-pulse h-3 w-8 rounded bg-bg-secondary" />
            <div className="motion-safe:animate-pulse h-6 w-16 rounded-full bg-bg-secondary" />
          </div>
        </div>
      </div>
    </div>
  )
}
