import { Link } from 'react-router-dom'
import { Clock } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatTime } from '../../lib'
import type { MockEpisode } from '../../lib'
import { EpisodeCover } from '../shared/EpisodeCover'

interface WeeklyCardProps {
  readonly ep: MockEpisode
  readonly duration?: number
  readonly className?: string
}

/**
 * 「本週受歡迎」單張卡片：封面置頂 + 標題 + CEFR/時長。
 *
 * 採 grid-cols-2 雙欄，行動裝置單卡寬 ~163px；封面 full-width aspect-square，
 * 標題 line-clamp-2，meta 精簡到一條 row（CEFR chip + duration）。
 *
 * 互動：active:scale-[0.98] 對齊 apple-design §1「回應立即」；border-color hover
 * 用 transition-[border-color,transform] 同步兩個屬性動畫，不引入多餘 transition。
 */
export function WeeklyCard({ ep, duration, className = '' }: WeeklyCardProps) {
  return (
    <Link
      to={`/player/${ep.id}`}
      data-testid="weekly-card"
      className={`block active:scale-[0.98] transition-transform duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-2xl ${className}`}
    >
      <article className="h-full rounded-2xl bg-bg-elevated border border-border p-2.5 sm:p-3 hover:border-accent/40 transition-colors duration-fast flex flex-col sm:flex-row sm:items-center gap-2.5">
        {/* 封面：mobile 頂部 aspect-square，desktop 左側 96px/104px 固定 */}
        <div className="relative shrink-0 w-full sm:w-24 sm:h-24 aspect-square sm:aspect-auto">
          <EpisodeCover episodeId={ep.id} topic={ep.topic} coverIcon={ep.coverIcon} size="hero" className="!w-full !h-full rounded-xl" />
        </div>

        {/* 內容區：mobile 位於封面下方，desktop 位於右側 */}
        <div className="flex-1 min-w-0 flex flex-col justify-between h-full py-0.5">
          <div>
            <div className="flex items-center gap-1.5 text-[10px] text-text-tertiary mb-1">
              <span className={`font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
                {ep.cefrLevel}
              </span>
              <span>· {TOPIC_LABELS[ep.topic]}</span>
            </div>
            <div className="text-xs sm:text-sm font-semibold text-text-primary line-clamp-2 leading-snug">
              {ep.title}
            </div>
          </div>

          <div className="mt-2 flex items-center gap-1 text-[11px] text-text-tertiary">
            <Clock size={11} />
            <span>{duration ? formatTime(duration) : '—'}</span>
          </div>
        </div>
      </article>
    </Link>
  )
}
