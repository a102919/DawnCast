import { useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown, History } from 'lucide-react'
import { Button, SectionLabel, EmptyState } from '../primitives'
import { EpisodeRow } from '../shared/EpisodeRow'
import { StatusBadge } from './StatusBadge'
import { useDailyOrder, useEpisodes } from '../../state'
import { formatDateZhTW } from '../../lib'
import type { DailyOrder } from '../../api'
import { useSprings } from '../../lib/motion'

/** 扁平倒序清單：列 ready/played/expired 的訂單。解析快取吃 DailyOrderProvider
 *  的 orderEpisodes（全 app 唯一一份）；只對從未嘗試過的訂單觸發解析——
 *  loading/failed/done 都不重打，天然終止（舊版自建 Map 會對解析失敗的訂單
 *  無限重打）。解析得到集數的用 EpisodeRow，其餘（expired／集數已下架）給
 *  精簡 fallback 卡，不再整列消失。 */
export function OrderHistoryList() {
  const springs = useSprings()
  const { episodes } = useEpisodes()
  const {
    history, historyExhausted, loadMoreHistory, orderEpisodes, resolveOrderEpisode,
  } = useDailyOrder()

  useEffect(() => {
    for (const order of history) {
      if (!orderEpisodes.has(order.id)) void resolveOrderEpisode(order.id)
    }
  }, [history, orderEpisodes, resolveOrderEpisode])

  return (
    <section className="space-y-3">
      <SectionLabel>歷史紀錄</SectionLabel>

      {history.length === 0 && (
        <EmptyState icon={History} title="還沒有完成的紀錄" description="點播一集開始吧" size="compact" />
      )}

      {history.length > 0 && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <AnimatePresence initial={false}>
              {history.map(order => {
                const entry = orderEpisodes.get(order.id)
                const episode = entry?.state === 'done' ? entry.episode : null
                const mockEp = episode ? episodes.find(e => e.id === episode.id) : undefined
                return (
                  <motion.div
                    key={order.id}
                    layout
                    initial={{ opacity: 0, scale: 0.98 }}
                    animate={{ opacity: 1, scale: 1, transition: springs.snappy }}
                    exit={{ opacity: 0, scale: 0.98, transition: springs.snappy }}
                  >
                    {mockEp ? (
                      <EpisodeRow ep={mockEp} variant="card" to={`/player?orderId=${order.id}`} />
                    ) : (
                      <OrderFallbackCard order={order} />
                    )}
                  </motion.div>
                )
              })}
            </AnimatePresence>
          </div>
          {!historyExhausted && (
            <div className="flex justify-center">
              <Button variant="ghost" size="sm" onClick={() => void loadMoreHistory()}>
                <ChevronDown size={12} aria-hidden />
                載入更多
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  )
}

/** 解析不到集數的訂單（expired、集數已下架、解析失敗）：顯示點播內容摘要＋
 *  狀態徽章，讓歷史完整可見，而不是安靜地少一列。 */
function OrderFallbackCard({ order }: { readonly order: DailyOrder }) {
  const title = order.specificRequest ?? order.selectedTopics.join('、')
  return (
    <div className="h-full rounded-xl border border-border bg-bg-secondary p-4 flex flex-col justify-between gap-2">
      <p className="text-sm font-medium text-text-secondary line-clamp-2">
        {title || '點播內容'}
      </p>
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-text-tertiary">{formatDateZhTW(order.date)}</span>
        <StatusBadge order={order} display="badge" />
      </div>
    </div>
  )
}
