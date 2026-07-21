import { useMemo } from 'react'
import type { Cue } from '../../types/episode'
import { splitTextToWords, formatTime } from '../../lib'
import { useVocab } from '../../state'
import { renderTokenized } from './renderTokenized'

interface CueLineProps {
  readonly cue: Cue
  readonly isActive: boolean
  readonly onWordClick: (word: string, cue: Cue) => void
  readonly onSeek: (time: number) => void
}

export function CueLine({ cue, isActive, onWordClick, onSeek }: CueLineProps) {
  const { isInVocab } = useVocab()
  const tokens = useMemo(() => splitTextToWords(cue.text), [cue.text])

  return (
    <div
      className={`p-3 rounded-md cursor-pointer transition-[background-color,border-color,transform] duration-fast active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        isActive ? 'bg-accent/8 border border-accent/20' : 'hover:bg-bg-secondary'
      }`}
      aria-current={isActive ? 'true' : undefined}
      role="button"
      tabIndex={0}
      onClick={() => onSeek(cue.start)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSeek(cue.start)
        }
      }}
    >
      <div className="flex items-baseline gap-2">
        <span className="text-xs font-mono text-text-tertiary shrink-0">
          {formatTime(cue.start)}
        </span>
        <span className={`text-xs font-medium shrink-0 ${isActive ? 'text-accent' : 'text-text-tertiary'}`}>
          {cue.speaker}
        </span>
      </div>
      <p className="text-sm leading-relaxed text-text-primary mt-0.5">
        {renderTokenized(cue.text, tokens, word => onWordClick(word, cue), isInVocab, { stopPropagation: true })}
      </p>
      <p className={`text-xs mt-0.5 ${isActive ? 'text-text-secondary' : 'text-text-tertiary'}`}>
        {cue.zh}
      </p>
    </div>
  )
}
