import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Play, Heart, Sparkles, Clock } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatDateZhTW, formatTime } from '../../lib'
import type { MockEpisode } from '../../lib'
import { useFavorites } from '../../state'
import { EpisodeCover } from '../shared/EpisodeCover'
import { useSprings } from '../../lib/motion'
import { HeroLayout } from './HeroLayout'

interface TodayHeroCardProps {
  readonly ep: MockEpisode
  readonly duration?: number
  /** 交付這集的點餐訂單 id，會附在 player 連結上（讓播完能標記這張訂單已播放） */
  readonly orderId: string
}

/**
 * Hero 區塊：今日 podcast 卡片。
 *
 * 排版策略（mobile-first，遵循 Apple Music / Spotify pattern）：
 * - < sm：垂直堆疊 — 封面在上 aspect-square 佔滿寬，文字區在下方不受擠壓
 * - ≥ sm：左右並排 — 封面 w-32 h-32 固定正方形，文字區 flex-1
 *
 * 互動遵循 apple-design §1「回應立即」：active:scale + motion.whileTap springs.press；
 * 進場用 springs.gentle（0.4s, bounce 0），reduced-motion 自動降級 tween。
 */
export function TodayHeroCard({ ep, duration, orderId }: TodayHeroCardProps) {
  const { favorites, toggle } = useFavorites()
  const isFav = favorites.has(ep.id)
  const springs = useSprings()

  return (
    <HeroLayout
      testId="today-hero"
      coverHref={`/player/${ep.id}?orderId=${orderId}`}
      coverAriaLabel={`立即收聽 ${ep.title}`}
      cover={
        <EpisodeCover
          episodeId={ep.id}
          topic={ep.topic}
          coverIcon={ep.coverIcon}
          size="hero"
          className="!w-32 !h-32 sm:!w-40 sm:!h-40 rounded-xl sm:rounded-2xl shadow-sm"
        />
      }
      badges={
        <>
          <span className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-[11px] font-semibold border border-accent/25 flex items-center gap-1">
            <Sparkles size={11} />
            <span>今日送達</span>
          </span>
          {ep.isFeatured && (
            <span className="px-2 py-0.5 rounded-full bg-bg-secondary text-text-tertiary text-[11px] font-medium border border-border">
              精選廣播
            </span>
          )}
        </>
      }
      title={ep.title}
      subtitle={ep.titleZh}
      meta={
        <>
          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
            {ep.cefrLevel}
          </span>
          <span>· {TOPIC_LABELS[ep.topic]}</span>
          <span className="flex items-center gap-0.5">
            · <Clock size={11} />
            <span>{duration ? formatTime(duration) : '—'}</span>
          </span>
          <span>· {formatDateZhTW(ep.publishedAt)}</span>
        </>
      }
      cta={
        <>
          <Link
            to={`/player/${ep.id}?orderId=${orderId}`}
            className="w-full justify-center px-3 sm:px-4 h-9 sm:h-10 rounded-full bg-accent text-white text-xs sm:text-sm font-semibold flex items-center gap-1.5 active:scale-[0.97] transition-transform shadow-sm whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
          >
            <Play size={15} fill="currentColor" />
            <span>立即收聽</span>
          </Link>
          <motion.button
            type="button"
            onClick={() => void toggle(ep.id)}
            whileTap={springs.reduce ? undefined : { scale: 0.94 }}
            transition={springs.press}
            aria-label={isFav ? '取消收藏' : '加入收藏'}
            aria-pressed={isFav}
            className={`w-full justify-center px-3 sm:px-4 h-9 sm:h-10 rounded-full text-xs sm:text-sm font-semibold flex items-center gap-1.5 active:scale-[0.97] transition-transform border whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
              isFav
                ? 'bg-accent/10 text-accent border-accent/30'
                : 'bg-bg-secondary text-text-primary border-border'
            }`}
          >
            <Heart size={15} fill={isFav ? 'currentColor' : 'none'} />
            <span>{isFav ? '已收藏' : '收藏'}</span>
          </motion.button>
        </>
      }
    />
  )
}
