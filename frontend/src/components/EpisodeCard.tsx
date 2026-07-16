import { Link } from 'react-router-dom'
import { Mic, Star, CheckCircle2, Heart, Play } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatDateZhTW } from '../routes/episodeData'
import type { MockEpisode } from '../routes/episodeData'
import { useListened, useFavorites } from '../state'

export function EpisodeCard({ ep }: { readonly ep: MockEpisode }) {
  const { listenedIds } = useListened()
  const isListened = listenedIds.has(ep.id)
  const { favorites, toggle } = useFavorites()
  const isFav = favorites.has(ep.id)

  return (
    <Link to="/player" className="block">
      <div className="relative p-4 rounded-lg border border-border bg-bg-primary hover:border-accent/40 hover:shadow-sm transition-all duration-fast group">
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
          className={`absolute bottom-2.5 right-2.5 z-10 p-1.5 rounded-full transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
            isFav
              ? 'bg-accent/10 text-accent hover:bg-accent/20'
              : 'bg-bg-secondary/80 text-text-tertiary hover:text-accent hover:bg-bg-secondary'
          }`}
        >
          <Heart size={12} fill={isFav ? 'currentColor' : 'none'} />
        </button>
        <div className="flex items-start justify-between gap-3">
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
          </div>
          <div className="shrink-0 w-8 h-8 rounded-full flex items-center justify-center bg-accent/10 text-accent group-hover:bg-accent group-hover:text-white transition-colors duration-fast">
            <Play size={14} fill="currentColor" />
          </div>
        </div>
        <div className="mt-2 flex items-center gap-2 text-xs text-text-tertiary">
          <Mic size={11} />
          <span>Alex &amp; Sarah</span>
          <span>·</span>
          <span>3 分鐘</span>
          <span>·</span>
          <span>{formatDateZhTW(ep.publishedAt)}</span>
        </div>
      </div>
    </Link>
  )
}