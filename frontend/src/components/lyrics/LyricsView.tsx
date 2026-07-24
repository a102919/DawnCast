import { useEffect, useMemo, useRef } from 'react'
import { useReducedMotion } from 'framer-motion'
import type { Cue } from '../../types/episode'
import { useVocab } from '../../state'
import { findActiveCueIndex, splitTextToWords, getCoverArt, coverArtBackground } from '../../lib'
import { renderTokenized } from '../shared/renderTokenized'
import { EpisodeCover } from '../shared/EpisodeCover'

const RESUME_AUTOSCROLL_MS = 3000

interface LyricsViewProps {
  readonly episodeId: string
  readonly episodeTitle: string
  readonly cues: readonly Cue[]
  readonly currentTime: number
  readonly onWordClick: (word: string, cue: Cue) => void
  readonly onCueClick?: (cue: Cue) => void
}

/** Apple Music 風大歌詞：當句大字置中、上下句半透、自動捲入中央；點字同樣接查詞。
 *
 * 設計要點：
 * - 三句可見（前一句 / 當句 / 後一句）；active 句左右 padding 大、字級 2xl、白色，
 *   其他句字級 base、半透明，與 Apple Music 一致。
 * - 自動捲：activeCueIdx 變動時 scrollIntoView({ block: 'center' })。使用者手動滾動
 *   （wheel / touchmove）時暫停自動捲動，停手 3 秒後自動恢復跟隨當句。
 */
export function LyricsView({ episodeId, episodeTitle, cues, currentTime, onWordClick, onCueClick }: LyricsViewProps) {
  const { isInVocab } = useVocab()
  const art = useMemo(() => getCoverArt(episodeId), [episodeId])
  const activeCueIdx = useMemo(() => findActiveCueIndex(cues, currentTime), [cues, currentTime])
  const activeRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const isUserScrollingRef = useRef(false)
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const handleUserScroll = () => {
      isUserScrollingRef.current = true
      clearTimeout(resumeTimerRef.current)
      resumeTimerRef.current = setTimeout(() => {
        isUserScrollingRef.current = false
      }, RESUME_AUTOSCROLL_MS)
    }
    container.addEventListener('wheel', handleUserScroll, { passive: true })
    container.addEventListener('touchmove', handleUserScroll, { passive: true })
    return () => {
      container.removeEventListener('wheel', handleUserScroll)
      container.removeEventListener('touchmove', handleUserScroll)
      clearTimeout(resumeTimerRef.current)
    }
  }, [])

  useEffect(() => {
    if (activeCueIdx < 0 || isUserScrollingRef.current) return
    activeRef.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' })
  }, [activeCueIdx, reduceMotion])

  return (
    <div className="relative h-full">
      {/* 封面藝術模糊背景（不隨捲動移動，比照 Apple Music 播放頁做法） */}
      <div
        className="absolute inset-0 opacity-35 blur-3xl scale-125 pointer-events-none"
        style={{ background: coverArtBackground(art) }}
      />
      <div className="absolute inset-0 lyrics-scrim pointer-events-none" />
      <div
        ref={containerRef}
        className="relative z-10 h-full overflow-y-auto px-6"
        aria-label="歌詞"
      >
        {/* Cover + title：當成第一個 scroll item，與歌詞一起滾動 */}
        <div className="flex items-center gap-4 pt-6 lg:pt-8 pb-4 shrink-0">
          <EpisodeCover episodeId={episodeId} size="lg" />
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-widest text-text-tertiary mb-1">
              Podcast
            </div>
            <h1 className="text-base lg:text-lg font-semibold truncate">{episodeTitle}</h1>
          </div>
        </div>

        <div className="space-y-6 md:space-y-10">
          {cues.map((cue, i) => {
            const isActive = i === activeCueIdx
            const tokens = splitTextToWords(cue.text)
            return (
              <div
                key={cue.index}
                ref={isActive ? activeRef : undefined}
                role="button"
                tabIndex={0}
                aria-label={`跳到 ${cue.speaker}: ${cue.text}`}
                className="cursor-pointer rounded-lg px-2 py-1 -mx-2 transition-all duration-300 ease-apple hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                onClick={() => onCueClick?.(cue)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onCueClick?.(cue)
                  }
                }}
              >
                {/* speaker 標籤（小字） */}
                <div
                  className={`text-xs font-medium uppercase tracking-wider mb-1 transition-opacity duration-300 ${
                    isActive ? 'opacity-60 text-text-tertiary' : 'opacity-40 text-text-tertiary'
                  }`}
                >
                  {cue.speaker}
                </div>

                {/* 英文主歌詞 */}
                <p
                  className={`leading-relaxed mb-1 ${
                    isActive
                      ? 'text-2xl md:text-3xl font-semibold text-text-primary'
                      : 'text-base text-text-tertiary opacity-60'
                  }`}
                >
                  {renderTokenized(
                    cue.text,
                    tokens,
                    word => onWordClick(word, cue),
                    isInVocab,
                    { stopPropagation: true, nonVocabHoverClass: 'hover:bg-bg-secondary/60' },
                  )}
                </p>

                {/* 中文翻譯 */}
                <p
                  className={`leading-relaxed ${
                    isActive
                      ? 'text-base text-accent/80'
                      : 'text-sm text-text-tertiary opacity-60'
                  }`}
                >
                  {cue.zh}
                </p>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
