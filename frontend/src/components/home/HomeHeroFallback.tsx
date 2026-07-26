import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Calendar, Play, Sparkles } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS } from '../../lib'
import type { MockEpisode } from '../../lib'
import { EpisodeCover } from '../shared/EpisodeCover'
import { useSprings } from '../../lib/motion'

interface HomeHeroFallbackProps {
  readonly featured: MockEpisode | undefined
}

/**
 * Hero 降級版：今日無 delivery 時顯示。CTA 連到 /daily 觸發點餐流程。
 * 與 TodayHeroCard 同 layout（橫向，封面 112/128 + 文字區）。
 */
export function HomeHeroFallback({ featured }: HomeHeroFallbackProps) {
  const springs = useSprings()

  if (!featured) return null

  return (
    <motion.article
      data-testid="today-hero-fallback"
      initial={{ opacity: 0, scale: 0.98, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={springs.gentle}
      className="relative h-full flex flex-col justify-center w-full"
    >
      <div className="w-full flex flex-col sm:flex-row items-center sm:items-stretch gap-4 flex-1">
        {/* ── 封面 ── */}
        <Link
          to={`/player/${featured.id}`}
          className="block shrink-0 active:scale-[0.98] transition-transform duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent self-center"
          aria-label={`試聽 ${featured.title}`}
        >
          <EpisodeCover
            episodeId={featured.id}
            topic={featured.topic}
            coverIcon={featured.coverIcon}
            size="hero"
            className="!w-32 !h-32 sm:!w-40 sm:!h-40 rounded-xl sm:rounded-2xl shadow-sm"
          />
        </Link>

        {/* ── 文字與內容區 ── */}
        <div className="w-full min-w-0 flex-1 flex flex-col justify-between text-center sm:text-left py-0.5">
          <div>
            <div className="flex items-center justify-center sm:justify-start gap-1.5 mb-1.5">
              <span className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-[11px] font-semibold border border-accent/25 flex items-center gap-1">
                <Sparkles size={11} />
                <span>精選試聽</span>
              </span>
            </div>

            <h1 className="text-base sm:text-lg font-bold tracking-tight leading-snug text-text-primary line-clamp-2">
              {featured.title}
            </h1>
            <p className="text-caption text-text-secondary truncate mt-0.5">
              {featured.titleZh}
            </p>
          </div>

          <div className="mt-3">
            <div className="text-caption text-text-tertiary flex items-center justify-center sm:justify-start gap-1.5 flex-wrap mb-2.5">
              <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[featured.cefrLevel]}`}>
                {featured.cefrLevel}
              </span>
              <span>· {TOPIC_LABELS[featured.topic]}</span>
            </div>

            {/* CTA 列：雙鈕對齊下欄網格 50%/50% 與 gap-3 */}
            <div className="grid grid-cols-2 gap-3 w-full">
              <Link
                to={`/player/${featured.id}`}
                className="w-full justify-center px-3 sm:px-4 h-9 sm:h-10 rounded-full bg-bg-secondary text-text-primary text-xs sm:text-sm font-semibold flex items-center gap-1.5 active:scale-[0.97] transition-transform border border-border whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
              >
                <Play size={15} fill="currentColor" />
                <span>先試聽</span>
              </Link>
              <Link
                to="/daily"
                className="w-full justify-center px-3 sm:px-4 h-9 sm:h-10 rounded-full bg-accent text-white text-xs sm:text-sm font-semibold flex items-center gap-1.5 active:scale-[0.97] transition-transform shadow-sm whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
              >
                <Calendar size={15} />
                <span>立即點餐</span>
              </Link>
            </div>
          </div>
        </div>
      </div>
    </motion.article>
  )
}
