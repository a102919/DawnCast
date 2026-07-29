import { useEffect, useState } from 'react'
import { SectionLabel } from '../primitives/SectionLabel'
import { WeeklyCard } from './WeeklyCard'
import { api } from '../../api'
import type { RecommendedEpisode } from '../../api'

/**
 * 首頁「根據你追蹤的頻道」：追蹤頻道裡還沒聽完的最新集數。
 *
 * 空清單時整個區塊不渲染——不畫空殼（apple-design「簡潔」：沒內容就不佔位）。
 * 卡片寬度 w-40（對齊原本 grid-cols-2 在手機寬度下的格子大小）sm: 起放寬到 w-72，
 * 讓 WeeklyCard 內部 sm:flex-row 的並排版面有足夠空間，不擠壓封面與文字。
 */
export function RecommendedRail() {
  const [episodes, setEpisodes] = useState<readonly RecommendedEpisode[]>([])

  useEffect(() => {
    let cancelled = false
    api
      .getRecommendedEpisodes()
      .then(list => {
        if (!cancelled) setEpisodes(list)
      })
      .catch(() => {
        // 推薦是錦上添花的區塊，失敗靜默略過，不影響首頁其餘內容載入。
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (episodes.length === 0) return null

  return (
    <section className="space-y-2.5">
      <SectionLabel>根據你追蹤的頻道</SectionLabel>
      <div className="flex gap-3 overflow-x-auto snap-x snap-mandatory -mx-4 px-4 pb-1">
        {episodes.map(ep => (
          <WeeklyCard
            key={ep.id}
            ep={ep}
            metaLabel={ep.channelName}
            className="w-40 sm:w-72 shrink-0 snap-start"
          />
        ))}
      </div>
    </section>
  )
}
