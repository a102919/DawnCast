import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Play, Heart, Sparkles, Clock } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatTime } from '../../lib'
import type { MockEpisode } from '../../lib'
import { useFavorites } from '../../state'
import { EpisodeCover } from '../shared/EpisodeCover'
import { useSprings } from '../../lib/motion'

interface TodayHeroCardProps {
  readonly ep: MockEpisode
  readonly duration?: number
  /** 對應今日 delivery 的日期，會附在 player 連結上 */
  readonly today: string
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
export function TodayHeroCard({ ep, duration, today }: TodayHeroCardProps) {
  const { favorites, toggle } = useFavorites()
  const isFav = favorites.has(ep.id)
  const springs = useSprings()

  return (
    <motion.article
      data-testid="today-hero"
      initial={{ opacity: 0, scale: 0.98, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={springs.gentle}
      className="material-regular rounded-3xl shadow-md relative overflow-hidden h-full flex flex-col"
    >
      <div className="flex flex-col sm:flex-row sm:gap-4 sm:p-4 flex-1">
        {/* ── 封面：mobile 縮成 50% 寬置中，desktop 128 置中對齊文字區 ── */}
        <Link
          to={`/player/${ep.id}?date=${today}`}
          className="block active:scale-[0.98] transition-transform duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent sm:rounded-xl sm:shrink-0 sm:self-center"
          aria-label={`立即收聽 ${ep.title}`}
        >
          <EpisodeCover
            episodeId={ep.id}
            size="hero"
            className="!w-1/2 !aspect-square mx-auto sm:!w-32 sm:!h-32 sm:!rounded-xl sm:!aspect-auto sm:!mx-0"
          />
        </Link>

        {/* ── 文字區（mobile 封面下方 p-4，desktop 右側 flex-1；內容整體垂直置中避免留白集中在底部）── */}
        <div className="min-w-0 flex-1 flex flex-col justify-center p-4 sm:p-0">
          <h1 className="text-base sm:text-lg font-bold tracking-tight leading-snug text-text-primary line-clamp-2 text-center sm:text-left">
            {ep.title}
          </h1>
          <p className="text-caption text-text-secondary truncate mt-0.5 text-center sm:text-left">
            {ep.titleZh}
          </p>

          <div className="text-caption text-text-tertiary mt-2 flex items-center justify-center sm:justify-start gap-1.5 flex-wrap">
            <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
              {ep.cefrLevel}
            </span>
            <span>· {TOPIC_LABELS[ep.topic]}</span>
            <span className="flex items-center gap-0.5">
              · <Clock size={11} />
              <span>{duration ? formatTime(duration) : '—'}</span>
            </span>
          </div>

          {/* CTA 列：Hero 卡片現在跟小卡並排，欄寬變窄，mobile 改直排避免按鈕擠爆 */}
          <div className="flex flex-col sm:flex-row gap-2 mt-3">
            <Link
              to={`/player/${ep.id}?date=${today}`}
              className="justify-center px-4 h-10 rounded-full bg-accent text-white text-sm font-semibold flex items-center gap-2 active:scale-[0.97] transition-transform shadow-sm whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 w-full sm:w-auto"
            >
              <Play size={16} fill="currentColor" />
              <span>立即收聽</span>
            </Link>
            <motion.button
              type="button"
              onClick={() => void toggle(ep.id)}
              whileTap={springs.reduce ? undefined : { scale: 0.94 }}
              transition={springs.press}
              aria-label={isFav ? '取消收藏' : '加入收藏'}
              aria-pressed={isFav}
              className={`justify-center px-4 h-10 rounded-full text-sm font-semibold flex items-center gap-2 active:scale-[0.97] transition-transform border whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent w-full sm:w-auto ${
                isFav
                  ? 'bg-accent/10 text-accent border-accent/30'
                  : 'bg-bg-secondary text-text-primary border-border'
              }`}
            >
              <Heart size={15} fill={isFav ? 'currentColor' : 'none'} />
              <span>{isFav ? '已收藏' : '收藏'}</span>
            </motion.button>
          </div>

          <div className="flex items-center justify-center sm:justify-start gap-1.5 mt-1">
            <span className="text-label tracking-label uppercase text-text-tertiary font-semibold">
              今日精選
            </span>
            {ep.isFeatured && (
              <span className="px-1.5 py-0.5 rounded-full bg-accent/10 text-accent text-[10px] font-medium border border-accent/30 flex items-center gap-0.5">
                <Sparkles size={9} />
                <span>精選試聽</span>
              </span>
            )}
          </div>
        </div>
      </div>
    </motion.article>
  )
}
