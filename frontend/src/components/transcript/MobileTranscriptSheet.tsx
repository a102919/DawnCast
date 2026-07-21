import { X } from 'lucide-react'
import { IconButton } from '../primitives/IconButton'
import { Sheet } from '../primitives/Sheet'
import { TranscriptPanel } from './TranscriptPanel'
import type { Cue } from '../../types/episode'

interface MobileTranscriptSheetProps {
  readonly isOpen: boolean
  readonly cues: readonly Cue[]
  readonly activeCueIdx: number
  readonly onWordClick: (word: string, cue: Cue) => void
  readonly onClose: () => void
}

export function MobileTranscriptSheet({
  isOpen,
  cues,
  activeCueIdx,
  onWordClick,
  onClose,
}: MobileTranscriptSheetProps) {
  return (
    <Sheet
      isOpen={isOpen}
      onClose={onClose}
      variant="bottom"
      ariaLabelledBy="mobile-transcript-sheet-title"
      maxHeight="70dvh"
    >
      {/* Title Bar */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
        <span id="mobile-transcript-sheet-title" className="text-sm font-semibold text-text-primary">逐字稿</span>
        <IconButton label="關閉逐字稿" size="sm" onClick={onClose}>
          <X size={16} />
        </IconButton>
      </div>

      {/* Transcript Content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        <TranscriptPanel
          cues={cues}
          activeCueIdx={activeCueIdx}
          onWordClick={onWordClick}
          hideHeader
        />
      </div>
    </Sheet>
  )
}
