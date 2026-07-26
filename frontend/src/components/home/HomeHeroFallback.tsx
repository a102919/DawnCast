import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Calendar, Play } from 'lucide-react'
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
      className="material-regular rounded-3xl shadow-md relative overflow-hidden h-full flex flex-col"
    >
      <div className="flex flex-col sm:flex-row sm:gap-4 sm:p-4 flex-1">
        {/* ── 封面：mobile 縮成 50% 寬置中，desktop 128 置中對齊文字區 ── */}
        <Link
          to={`/player/${featured.id}`}
          className="block active:scale-[0.98] transition-transform duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent sm:rounded-xl sm:shrink-0 sm:self-center"
          aria-label={`試聽 ${featured.title}`}
        >
          <EpisodeCover
            episodeId={featured.id}
            size="hero"
            className="!w-1/2 !aspect-square mx-auto sm:!w-32 sm:!h-32 sm:!rounded-xl sm:!aspect-auto sm:!mx-0"
          />
        </Link>

        {/* ── 文字區（mobile 封面下方 p-4，desktop 右側 flex-1；內容整體垂直置中避免留白集中在底部）── */}
        <div className="min-w-0 flex-1 flex flex-col justify-center p-4 sm:p-0">
          <div className="flex items-center justify-center sm:justify-start gap-1.5 mb-1.5">
            <span className="text-label tracking-label uppercase text-text-tertiary font-semibold">
              今日尚未送達
            </span>
            {featured.isFeatured && (
              <span className="px-1.5 py-0.5 rounded-full bg-accent/10 text-accent text-[10px] font-medium border border-accent/30">
                精選試聽
              </span>
            )}
          </div>

          <h1 className="text-base sm:text-lg font-bold tracking-tight leading-snug text-text-primary line-clamp-2 text-center sm:text-left">
            {featured.title}
          </h1>
          <p className="text-caption text-text-secondary truncate mt-0.5 text-center sm:text-left">
            {featured.titleZh}
          </p>

          <p className="text-caption text-text-tertiary mt-2 line-clamp-2 text-center sm:text-left">
            點餐後明日 07:00 自動送達，先試聽這集吧
          </p>

          <div className="text-caption text-text-tertiary mt-1.5 flex items-center justify-center sm:justify-start gap-1.5 flex-wrap">
            <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[featured.cefrLevel]}`}>
              {featured.cefrLevel}
            </span>
            <span>· {TOPIC_LABELS[featured.topic]}</span>
          </div>

          <div className="flex flex-col sm:flex-row gap-2 mt-3">
            <Link
              to={`/player/${featured.id}`}
              className="justify-center px-4 h-10 rounded-full bg-bg-secondary text-text-primary text-sm font-semibold flex items-center gap-2 active:scale-[0.97] transition-transform whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 w-full sm:w-auto"
            >
              <Play size={16} fill="currentColor" />
              <span>先試聽</span>
            </Link>
            <Link
              to="/daily"
              className="justify-center px-4 h-10 rounded-full bg-accent text-white text-sm font-semibold flex items-center gap-2 active:scale-[0.97] transition-transform shadow-sm whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 w-full sm:w-auto"
            >
              <Calendar size={16} />
              <span>立即點餐</span>
            </Link>
          </div>
        </div>
      </div>
    </motion.article>
  )
}
