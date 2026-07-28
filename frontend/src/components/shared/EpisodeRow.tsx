import { Link } from 'react-router-dom'
import { Clock, Star, CheckCircle2, Heart, Play } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatDateZhTW, formatTime } from '../../lib'
import type { MockEpisode } from '../../lib'
import { useActivity, useFavorites } from '../../state'
import { EpisodeCover } from './EpisodeCover'

interface EpisodeRowProps {
  readonly ep: MockEpisode
  readonly variant: 'card' | 'compact'
  /** 從 cues 末段推算的集數時長（秒）；card 顯示用 */
  readonly duration?: number
}

export function EpisodeRow({ ep, variant, duration }: EpisodeRowProps) {
  if (variant === 'compact') return <CompactRow ep={ep} />
  return <CardRow ep={ep} duration={duration} />
}

function CardRow({ ep, duration }: { readonly ep: MockEpisode; readonly duration?: number }) {
  const { listenedEpisodeIds } = useActivity()
  const isListened = listenedEpisodeIds.has(ep.id)
  const { favorites, toggle } = useFavorites()
  const isFav = favorites.has(ep.id)

  return (
    <Link to={`/player/${ep.id}`} className="block">
      <div className="relative p-4 rounded-lg border border-border bg-bg-primary hover:border-accent/40 hover:shadow-sm active:scale-[0.99] transition-[border-color,box-shadow,transform] duration-fast group">
        {ep.isFeatured && (
          <div className="absolute top-2.5 right-2.5 z-10 flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-accent/10 border border-accent/30 text-accent">
            <Star size={9} />
            <span className="text-[10px] font-medium">精選試聽</span>
          </div>
        )}
        {isListened && (
          <div className="absolute top-2.5 left-2.5 z-10 flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-success/10 border border-success/30 text-success">
            <CheckCircle2 size={9} />
            <span className="text-[10px] font-medium">已聽完</span>
          </div>
        )}
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            void toggle(ep.id)
          }}
          aria-label={isFav ? '取消收藏' : '加入收藏'}
          aria-pressed={isFav}
          className={`absolute bottom-2.5 right-2.5 z-10 p-1.5 rounded-full transition-[background-color,color,transform] duration-fast active:scale-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
            isFav
              ? 'bg-accent/10 text-accent hover:bg-accent/20'
              : 'bg-bg-secondary/80 text-text-tertiary hover:text-accent hover:bg-bg-secondary'
          }`}
        >
          <Heart size={12} fill={isFav ? 'currentColor' : 'none'} />
        </button>
        <div className="flex gap-3">
          <div className="relative">
            <EpisodeCover episodeId={ep.id} topic={ep.topic} coverIcon={ep.coverIcon} size="md" />
            <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full material-thin ring-1 ring-border flex items-center justify-center text-accent group-hover:bg-accent group-hover:text-white group-hover:ring-accent transition-colors duration-fast">
              <Play size={11} fill="currentColor" />
            </div>
          </div>
          <div className="min-w-0 flex-1">
            <EpisodeMeta ep={ep} />
          </div>
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-text-tertiary">
          <Clock size={11} />
          <span>{duration ? formatTime(duration) : '—'}</span>
          <span>·</span>
          <span>{formatDateZhTW(ep.publishedAt)}</span>
        </div>
      </div>
    </Link>
  )
}

function CompactRow({ ep }: { readonly ep: MockEpisode }) {
  return (
    <Link to={`/player/${ep.id}`}>
      <div className="relative p-4 rounded-lg border border-border bg-bg-primary hover:border-accent/40 hover:shadow-sm active:scale-[0.99] transition-[border-color,box-shadow,transform] duration-fast">
        <div className="flex items-start justify-between gap-3 pr-16">
          <div className="min-w-0">
            <EpisodeMeta ep={ep} />
            <div className="text-xs text-text-tertiary mt-2">{formatDateZhTW(ep.publishedAt)}</div>
          </div>
        </div>
        <div className="absolute bottom-3 right-3 w-7 h-7 rounded-full flex items-center justify-center bg-accent/10 text-accent">
          <Play size={12} fill="currentColor" />
        </div>
      </div>
    </Link>
  )
}

function EpisodeMeta({ ep }: { readonly ep: MockEpisode }) {
  return (
    <>
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-xs text-text-tertiary">E{ep.episode}</span>
        <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
          {ep.cefrLevel}
        </span>
        <span className="text-xs text-text-tertiary">{TOPIC_LABELS[ep.topic]}</span>
      </div>
      <div className="font-medium text-text-primary text-sm leading-snug">{ep.title}</div>
      <div className="text-xs text-text-secondary mt-0.5">{ep.titleZh}</div>
    </>
  )
}
