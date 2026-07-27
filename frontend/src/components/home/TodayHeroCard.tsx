import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Play, Heart, Sparkles, Clock } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS, formatDateZhTW, formatTime } from '../../lib'
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
      className="relative h-full flex flex-col justify-center w-full"
    >
      <div className="w-full flex flex-col sm:flex-row items-center sm:items-stretch gap-4 flex-1">
        {/* ── 封面：mobile 128px 置中，desktop 160px/176px 保持大封面比例 ── */}
        <Link
          to={`/player/${ep.id}?date=${today}`}
          className="block shrink-0 active:scale-[0.98] transition-transform duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent self-center"
          aria-label={`立即收聽 ${ep.title}`}
        >
          <EpisodeCover
            episodeId={ep.id}
            topic={ep.topic}
            coverIcon={ep.coverIcon}
            size="hero"
            className="!w-32 !h-32 sm:!w-40 sm:!h-40 rounded-xl sm:rounded-2xl shadow-sm"
          />
        </Link>

        {/* ── 文字與內容區 ── */}
        <div className="w-full min-w-0 flex-1 flex flex-col justify-between text-center sm:text-left py-0.5">
          <div>
            {/* 標籤列：頂部顯示送達狀態 */}
            <div className="flex items-center justify-center sm:justify-start gap-1.5 mb-1.5">
              <span className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-[11px] font-semibold border border-accent/25 flex items-center gap-1">
                <Sparkles size={11} />
                <span>今日送達</span>
              </span>
              {ep.isFeatured && (
                <span className="px-2 py-0.5 rounded-full bg-bg-secondary text-text-tertiary text-[11px] font-medium border border-border">
                  精選廣播
                </span>
              )}
            </div>

            <h1 className="text-base sm:text-lg font-bold tracking-tight leading-snug text-text-primary line-clamp-2">
              {ep.title}
            </h1>
            <p className="text-caption text-text-secondary truncate mt-0.5">
              {ep.titleZh}
            </p>
          </div>

          <div className="mt-3">
            <div className="text-caption text-text-tertiary flex items-center justify-center sm:justify-start gap-1.5 flex-wrap mb-2.5">
              <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[ep.cefrLevel]}`}>
                {ep.cefrLevel}
              </span>
              <span>· {TOPIC_LABELS[ep.topic]}</span>
              <span className="flex items-center gap-0.5">
                · <Clock size={11} />
                <span>{duration ? formatTime(duration) : '—'}</span>
              </span>
              <span>· {formatDateZhTW(ep.publishedAt)}</span>
            </div>

            {/* CTA 列：雙鈕對齊下欄網格 50%/50% 與 gap-3 */}
            <div className="grid grid-cols-2 gap-3 w-full">
              <Link
                to={`/player/${ep.id}?date=${today}`}
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
            </div>
          </div>
        </div>
      </div>
    </motion.article>
  )
}
