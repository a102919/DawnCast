import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ChevronRight, RadioTower } from 'lucide-react'
import { EmptyState } from '../primitives/EmptyState'
import { SectionLabel } from '../primitives/SectionLabel'
import { ChannelCover } from '../shared/ChannelCover'
import { useSprings } from '../../lib/motion'
import { api } from '../../api'
import type { ChannelPublic } from '../../api'

/** 首頁預覽最多顯示幾個頻道；超過才顯示「全部」連到完整清單。 */
const PREVIEW_LIMIT = 4

/**
 * 首頁「頻道」：直接列出頻道目錄前 4 個，點卡片進頻道詳情頁。
 * 超過 4 個頻道才顯示「全部」連到 /channels 完整清單——只有一頁時
 * 「全部」連去同樣的內容沒有意義（apple-design「簡潔」：每個元素都要有存在理由）。
 *
 * 原生 overflow-x-auto + snap-x（不用 JS 拖曳庫）：捲動本身零延遲，符合
 * apple-design §1「回應立即」；-mx-4 px-4 讓卡片捲到底時能貼齊頁面邊緣
 * （對齊 HomeRoute 外層容器的 px-4）。
 */
export function ChannelsRail() {
  const [channels, setChannels] = useState<readonly ChannelPublic[] | null>(null)
  const springs = useSprings()

  useEffect(() => {
    let cancelled = false
    api
      .listChannels()
      .then(list => {
        if (!cancelled) setChannels(list)
      })
      .catch(() => {
        // 頻道區塊是首頁錦上添花的一角（同 RecommendedRail 的處理方式），
        // 失敗靜默略過，不影響首頁其餘內容載入。
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (channels === null) {
    return (
      <section className="space-y-2.5 mt-2">
        <SectionLabel size="headline">頻道</SectionLabel>
        <div className="flex gap-3 overflow-x-auto -mx-4 px-4 pb-1 scroll-pl-4">
          {Array.from({ length: PREVIEW_LIMIT }).map((_, i) => (
            <div key={i} className="flex flex-col items-center gap-1.5 shrink-0 w-28 sm:w-32">
              <div className="motion-safe:animate-pulse w-28 h-28 sm:w-32 sm:h-32 rounded-2xl bg-bg-secondary" />
              <div className="motion-safe:animate-pulse h-3 w-16 rounded bg-bg-secondary" />
            </div>
          ))}
        </div>
      </section>
    )
  }

  if (channels.length === 0) {
    return <EmptyState icon={RadioTower} title="目前還沒有任何頻道" size="compact" />
  }

  const preview = channels.slice(0, PREVIEW_LIMIT)

  return (
    <section className="space-y-2.5 mt-2">
      <div className="flex items-center justify-between">
        <SectionLabel size="headline">頻道</SectionLabel>
        {channels.length > PREVIEW_LIMIT && (
          <Link
            to="/channels"
            className="flex items-center gap-0.5 text-xs font-medium text-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
          >
            全部
            <ChevronRight size={14} />
          </Link>
        )}
      </div>
      <div className="flex gap-3 overflow-x-auto snap-x snap-mandatory -mx-4 px-4 pb-1 scroll-pl-4">
        {preview.map(channel => (
          <Link
            key={channel.slug}
            to={`/channels/${channel.slug}`}
            className="shrink-0 w-28 sm:w-32 snap-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-xl"
          >
            <motion.div
              className="flex flex-col items-center gap-1.5"
              whileTap={springs.reduce ? undefined : { scale: 0.94 }}
              transition={springs.press}
            >
              <ChannelCover url={channel.coverImageUrl} slug={channel.slug} topic={channel.topic} size="lg" className="w-28 h-28 sm:w-32 sm:h-32" />
              <span className="text-xs font-medium text-text-primary text-center line-clamp-2 leading-tight">
                {channel.name}
              </span>
              <span className="text-[11px] text-text-tertiary">{channel.episodeCount} 集</span>
            </motion.div>
          </Link>
        ))}
      </div>
    </section>
  )
}
