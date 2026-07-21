import { Link } from 'react-router-dom'
import { Mic, Star, CheckCircle2, Heart, Play } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatDateZhTW } from '../../lib'
import type { MockEpisode } from '../../lib'
import { useActivity, useFavorites } from '../../state'
import { EpisodeCover } from './EpisodeCover'

interface EpisodeRowProps {
  readonly ep: MockEpisode
  readonly variant: 'card' | 'hero' | 'compact'
  /** hero 限定：完整 Episode 載入後的真實標題；null 顯示 skeleton，省略則直接用 ep.title */
  readonly title?: string | null
}

export function EpisodeRow({ ep, variant, title }: EpisodeRowProps) {
  if (variant === 'hero') return <HeroRow ep={ep} title={title} />
  if (variant === 'compact') return <CompactRow ep={ep} />
  return <CardRow ep={ep} />
}

function CardRow({ ep }: { readonly ep: MockEpisode }) {
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
            <EpisodeCover episodeId={ep.id} size="md" />
            <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full material-thin ring-1 ring-border flex items-center justify-center text-accent group-hover:bg-accent group-hover:text-white group-hover:ring-accent transition-colors duration-fast">
              <Play size={11} fill="currentColor" />
            </div>
          </div>
          <div className="min-w-0 flex-1">
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

function HeroRow({ ep, title }: { readonly ep: MockEpisode; readonly title?: string | null }) {
  const isLoading = title === null
  return (
    <Link to={`/player/${ep.id}`} className="block">
      <div className="p-5 rounded-xl ring-1 ring-accent/20 material-thin hover:bg-bg-secondary/40 shadow-md active:scale-[0.99] transition-[background-color,transform] duration-fast group">
        <div className="flex items-center gap-4">
          <div className="relative shrink-0">
            <EpisodeCover episodeId={ep.id} size="lg" />
            <div className="absolute -bottom-1.5 -right-1.5 w-9 h-9 rounded-full bg-accent shadow-sm flex items-center justify-center text-white group-hover:scale-105 transition-transform duration-fast">
              <Play size={16} fill="currentColor" />
            </div>
          </div>
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-2 text-xs text-text-secondary">
              <span>E{ep.episode}</span>
              <span>·</span>
              <span>{TOPIC_LABELS[ep.topic]}</span>
              <span>·</span>
              <span>3 分鐘</span>
            </div>
            {isLoading ? (
              <>
                <div className="h-6 w-48 rounded bg-bg-secondary animate-pulse" />
                <div className="h-4 w-36 rounded bg-bg-secondary animate-pulse" />
              </>
            ) : (
              <>
                <div className="font-semibold text-text-primary text-lg leading-tight">
                  {title ?? ep.title}
                </div>
                <div className="text-sm text-text-secondary">{ep.titleZh}</div>
              </>
            )}
            <div className="flex items-center gap-1.5 text-xs text-text-tertiary mt-1">
              <Mic size={11} />
              <span>Alex &amp; Sarah · {formatDateZhTW(ep.publishedAt)}</span>
            </div>
          </div>
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
            <div className="flex items-center gap-1.5 mb-1.5">
              <span className="text-xs text-text-tertiary">E{ep.episode}</span>
              <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
                {ep.cefrLevel}
              </span>
              <span className="text-xs text-text-tertiary">{TOPIC_LABELS[ep.topic]}</span>
            </div>
            <div className="font-medium text-text-primary text-sm leading-snug">{ep.title}</div>
            <div className="text-xs text-text-secondary mt-0.5">{ep.titleZh}</div>
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
