import { Link } from 'react-router-dom'
import { Play, Sparkles } from 'lucide-react'
import { CEFR_COLOR, TOPIC_LABELS } from '../../lib'
import type { MockEpisode } from '../../lib'
import { EpisodeCover } from '../shared/EpisodeCover'
import { HeroLayout } from './HeroLayout'

interface HomeHeroFallbackProps {
  readonly featured: MockEpisode | undefined
}

/**
 * Hero 降級版：沒有 ready 訂單時顯示精選集，CTA 直接連到該集播放頁。
 * 與 TodayHeroCard 同 layout（橫向，封面 112/128 + 文字區）。
 */
export function HomeHeroFallback({ featured }: HomeHeroFallbackProps) {
  if (!featured) return null

  return (
    <HeroLayout
      testId="today-hero-fallback"
      coverHref={`/player/${featured.id}`}
      coverAriaLabel={`試聽 ${featured.title}`}
      cover={
        <EpisodeCover
          episodeId={featured.id}
          topic={featured.topic}
          coverIcon={featured.coverIcon}
          size="hero"
          className="!w-32 !h-32 sm:!w-40 sm:!h-40 rounded-xl sm:rounded-2xl shadow-sm"
        />
      }
      badges={
        <span className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-[11px] font-semibold border border-accent/25 flex items-center gap-1">
          <Sparkles size={11} />
          <span>精選試聽</span>
        </span>
      }
      title={featured.title}
      subtitle={featured.titleZh}
      meta={
        <>
          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[featured.cefrLevel]}`}>
            {featured.cefrLevel}
          </span>
          <span>· {TOPIC_LABELS[featured.topic]}</span>
        </>
      }
      cta={
        <Link
          to={`/player/${featured.id}`}
          className="col-span-2 w-full justify-center px-3 sm:px-4 h-9 sm:h-10 rounded-full bg-accent text-white text-xs sm:text-sm font-semibold flex items-center gap-1.5 active:scale-[0.97] transition-transform shadow-sm whitespace-nowrap focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
        >
          <Play size={15} fill="currentColor" />
          <span>立即收聽</span>
        </Link>
      }
    />
  )
}
