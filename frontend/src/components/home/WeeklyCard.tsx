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
      <article className="h-full rounded-2xl bg-bg-elevated border border-border p-2 hover:border-accent/40 transition-[border-color] duration-fast">
        <div className="relative">
          {/* size="hero" 預設 w-full aspect-square，這裡只要把 rounded-2xl 改成 lg */}
          <EpisodeCover episodeId={ep.id} size="hero" className="rounded-lg" />
        </div>
        <div className="mt-2 flex items-center justify-between text-[10px] text-text-tertiary">
          <span className={`font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
            {ep.cefrLevel}
          </span>
          <span className="flex items-center gap-0.5">
            <Clock size={10} />
            <span>{duration ? formatTime(duration) : '—'}</span>
          </span>
        </div>
        <div className="mt-1.5 text-[13px] font-medium text-text-primary line-clamp-2 leading-snug">
          {ep.title}
        </div>
        <div className="text-[11px] text-text-secondary truncate mt-0.5">
          {TOPIC_LABELS[ep.topic]}
        </div>
      </article>
    </Link>
  )
}
