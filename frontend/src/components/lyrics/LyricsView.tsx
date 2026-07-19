import { useEffect, useMemo, useRef } from 'react'
import type { Cue } from '../../types/episode'
import { useVocab } from '../../state'
import { findActiveCueIndex, splitTextToWords } from '../../lib'
import { renderTokenized } from '../transcript/renderTokenized'

interface LyricsViewProps {
  readonly cues: readonly Cue[]
  readonly currentTime: number
  readonly onWordClick: (word: string, cue: Cue) => void
}

/** Apple Music 風大歌詞：當句大字置中、上下句半透、自動捲入中央；點字同樣接查詞。
 *
 * 設計要點：
 * - 三句可見（前一句 / 當句 / 後一句）；active 句左右 padding 大、字級 2xl、白色，
 *   其他句字級 base、半透明，與 Apple Music 一致。
 * - 自動捲：activeCueIdx 變動時 scrollIntoView({ block: 'center' })，用 behavior: 'smooth'。
 *   使用者手動捲動不會被覆寫（沒裝 wheel listener），播放中切到下一句仍會自動對齊。
 */
export function LyricsView({ cues, currentTime, onWordClick }: LyricsViewProps) {
  const { isInVocab } = useVocab()
  const activeCueIdx = useMemo(() => findActiveCueIndex(cues, currentTime), [cues, currentTime])
  const activeRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (activeCueIdx < 0) return
    activeRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [activeCueIdx])

  return (
    <div
      ref={containerRef}
      className="h-full overflow-y-auto px-6 py-[40vh]"
      aria-label="歌詞"
    >
      {cues.map((cue, i) => {
        const isActive = i === activeCueIdx
        const tokens = splitTextToWords(cue.text)
        return (
          <div
            key={cue.index}
            ref={isActive ? activeRef : undefined}
            className="transition-all duration-300 ease-apple"
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
                  ? 'text-2xl md:text-3xl font-semibold text-white'
                  : 'text-base text-white/40'
              }`}
            >
              {renderTokenized(
                cue.text,
                tokens,
                word => onWordClick(word, cue),
                isInVocab,
                { nonVocabHoverClass: 'hover:bg-white/10' },
              )}
            </p>

            {/* 中文翻譯 */}
            <p
              className={`leading-relaxed ${
                isActive
                  ? 'text-base text-accent/80'
                  : 'text-sm text-white/25'
              }`}
            >
              {cue.zh}
            </p>

            <div className="h-12 md:h-16" />
          </div>
        )
      })}
    </div>
  )
}
