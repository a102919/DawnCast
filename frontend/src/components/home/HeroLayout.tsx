import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { useSprings } from '../../lib/motion'

interface HeroLayoutProps {
  readonly testId: string
  readonly coverHref: string
  readonly coverAriaLabel: string
  readonly cover: ReactNode
  readonly badges: ReactNode
  readonly title: ReactNode
  readonly subtitle: ReactNode
  readonly meta: ReactNode
  readonly cta: ReactNode
}

/**
 * Hero 排版骨架：TodayHeroCard 與 HomeHeroFallback 共用的外層結構與動效。
 * 純展示型元件，不含資料邏輯；各自的資料、CTA 按鈕組由呼叫端傳入。
 */
export function HeroLayout({
  testId,
  coverHref,
  coverAriaLabel,
  cover,
  badges,
  title,
  subtitle,
  meta,
  cta,
}: HeroLayoutProps) {
  const springs = useSprings()

  return (
    <motion.article
      data-testid={testId}
      initial={{ opacity: 0, scale: 0.98, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={springs.gentle}
      className="relative h-full flex flex-col justify-center w-full"
    >
      <div className="w-full flex flex-col sm:flex-row items-center sm:items-stretch gap-4 flex-1">
        {/* ── 封面：mobile 128px 置中，desktop 160px/176px 保持大封面比例 ── */}
        <Link
          to={coverHref}
          className="block shrink-0 active:scale-[0.98] transition-transform duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent self-center"
          aria-label={coverAriaLabel}
        >
          {cover}
        </Link>

        {/* ── 文字與內容區 ── */}
        <div className="w-full min-w-0 flex-1 flex flex-col justify-between text-center sm:text-left py-0.5">
          <div>
            {/* 標籤列：頂部顯示送達狀態 */}
            <div className="flex items-center justify-center sm:justify-start gap-1.5 mb-1.5">
              {badges}
            </div>

            <h1 className="text-base sm:text-lg font-bold tracking-tight leading-snug text-text-primary line-clamp-2">
              {title}
            </h1>
            <p className="text-caption text-text-secondary truncate mt-0.5">
              {subtitle}
            </p>
          </div>

          <div className="mt-3">
            <div className="text-caption text-text-tertiary flex items-center justify-center sm:justify-start gap-1.5 flex-wrap mb-2.5">
              {meta}
            </div>

            {/* CTA 列：雙鈕對齊下欄網格 50%/50% 與 gap-3 */}
            <div className="grid grid-cols-2 gap-3 w-full">
              {cta}
            </div>
          </div>
        </div>
      </div>
    </motion.article>
  )
}
