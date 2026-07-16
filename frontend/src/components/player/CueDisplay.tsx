import { useMemo } from 'react'
import type { Cue } from '../../types/episode'
import { splitTextToWords } from '../../lib'
import { useVocab, useSettings } from '../../state'
import { renderTokenized } from '../transcript/renderTokenized'

const SPEAKER_COLORS = ['text-accent', 'text-success'] as const

/** 將 speaker 名稱穩定對映到顏色 token */
function getSpeakerColor(speaker: string, allSpeakers: readonly string[]): string {
  const idx = allSpeakers.indexOf(speaker)
  return SPEAKER_COLORS[idx % SPEAKER_COLORS.length] ?? SPEAKER_COLORS[0]
}

const FONT_SIZE_EN: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'text-sm',
  md: 'text-base',
  lg: 'text-lg',
}

const FONT_SIZE_ZH: Record<'sm' | 'md' | 'lg', string> = {
  sm: 'text-xs',
  md: 'text-sm',
  lg: 'text-base',
}

interface CueDisplayProps {
  readonly cue: Cue
  readonly onWordClick: (word: string, cue: Cue) => void
  readonly allSpeakers?: readonly string[]
}

export function CueDisplay({ cue, onWordClick, allSpeakers }: CueDisplayProps) {
  const { isInVocab } = useVocab()
  const { settings } = useSettings()
  const tokens = useMemo(() => splitTextToWords(cue.text), [cue.text])

  const speakerColor = allSpeakers && allSpeakers.length > 0
    ? getSpeakerColor(cue.speaker, allSpeakers)
    : 'text-text-tertiary'

  return (
    <div className="bg-bg-secondary rounded-lg p-4 space-y-3">
      <div className={`text-xs font-medium ${speakerColor} uppercase tracking-wide`}>
        {cue.speaker}
      </div>

      {/* 英文，可點擊 */}
      <p className={`${FONT_SIZE_EN[settings.fontSize]} leading-relaxed text-text-primary`}>
        {renderTokenized(cue.text, tokens, word => onWordClick(word, cue), isInVocab, { nonVocabHoverClass: 'hover:bg-accent/10' })}
      </p>

      {/* 中文翻譯 */}
      <p className={`${FONT_SIZE_ZH[settings.fontSize]} leading-relaxed text-text-secondary`}>
        {cue.zh}
      </p>
    </div>
  )
}
