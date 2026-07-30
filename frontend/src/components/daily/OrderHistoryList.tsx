import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown, History } from 'lucide-react'
import { Button, SectionLabel, EmptyState } from '../primitives'
import { EpisodeRow } from '../shared/EpisodeRow'
import { useEpisodes } from '../../state'
import { api } from '../../api'
import type { DailyOrder } from '../../api'
import type { MockEpisode } from '../../lib'
import { useSprings } from '../../lib/motion'

interface OrderHistoryListProps {
  readonly history: readonly DailyOrder[]
  readonly onLoadMore: () => void
}

/** 扁平倒序清單：只列生成完成（ready）以上的訂單。卡片統一用首頁「選擇 podcast」
 *  的 EpisodeRow（variant="card"），不再自己刻一套列表樣式。 */
export function OrderHistoryList({ history, onLoadMore }: OrderHistoryListProps) {
  const springs = useSprings()
  const { episodes } = useEpisodes()
  const [delivered, setDelivered] = useState<ReadonlyMap<string, MockEpisode>>(new Map())

  useEffect(() => {
    const missing = history.filter(o => !delivered.has(o.id))
    if (missing.length === 0) return
    let cancelled = false
    void (async () => {
      const entries = await Promise.all(missing.map(async (o) => {
        const ep = await api.getDeliveredEpisode(o.id).catch(() => null)
        const match = ep ? episodes.find(e => e.id === ep.id) : undefined
        return match ? ([o.id, match] as const) : null
      }))
      if (cancelled) return
      setDelivered(prev => {
        const next = new Map(prev)
        for (const entry of entries) {
          if (entry) next.set(entry[0], entry[1])
        }
        return next
      })
    })()
    return () => {
      cancelled = true
    }
  }, [history, episodes, delivered])

  const resolved = history
    .map(order => ({ order, ep: delivered.get(order.id) }))
    .filter((row): row is { order: DailyOrder; ep: MockEpisode } => row.ep !== undefined)

  return (
    <section className="space-y-3">
      <SectionLabel>歷史紀錄</SectionLabel>

      {history.length === 0 && (
        <EmptyState icon={History} title="還沒有播放完成的紀錄" description="點播一集開始吧" size="compact" />
      )}

      {history.length > 0 && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <AnimatePresence initial={false}>
              {resolved.map(({ order, ep }) => (
                <motion.div
                  key={order.id}
                  layout
                  initial={{ opacity: 0, scale: 0.98 }}
                  animate={{ opacity: 1, scale: 1, transition: springs.snappy }}
                  exit={{ opacity: 0, scale: 0.98, transition: springs.snappy }}
                >
                  <EpisodeRow ep={ep} variant="card" to={`/player?orderId=${order.id}`} />
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
          {/* ponytail: 沒有精準判斷是否已到底就隱藏按鈕（loadMoreHistory 已到底時
              是 no-op），使用者按了沒反應再加 exhausted 旗標。 */}
          <div className="flex justify-center">
            <Button variant="ghost" size="sm" onClick={onLoadMore}>
              <ChevronDown size={12} aria-hidden />
              載入更多
            </Button>
          </div>
        </>
      )}
    </section>
  )
}
