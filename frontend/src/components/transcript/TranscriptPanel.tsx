import { useEffect, useRef } from 'react'
import type { Cue } from '../../types/episode'
import { CueLine } from './CueLine'
import { usePlayer } from '../../state'

interface TranscriptPanelProps {
  readonly cues: readonly Cue[]
  readonly activeCueIdx: number
  readonly onWordClick: (word: string, cue: Cue) => void
  readonly onCueClick?: (cue: Cue) => void
  readonly hideHeader?: boolean
}

export function TranscriptPanel({ cues, activeCueIdx, onWordClick, onCueClick, hideHeader }: TranscriptPanelProps) {
  const { seekTo } = usePlayer()
  const activeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [activeCueIdx])

  return (
    <div className="h-full flex flex-col">
      {!hideHeader && (
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-sm font-semibold text-text-primary">逐字稿</h2>
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-0.5">
        {cues.map((cue, i) => (
          <div key={cue.index} ref={i === activeCueIdx ? activeRef : undefined}>
            <CueLine
              cue={cue}
              isActive={i === activeCueIdx}
              onWordClick={onWordClick}
              onSeek={(time) => {
                seekTo(time)
                onCueClick?.(cue)
              }}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
