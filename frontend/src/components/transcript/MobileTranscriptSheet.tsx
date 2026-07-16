import { useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X } from 'lucide-react'
import { IconButton } from '../primitives/IconButton'
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
  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Overlay */}
          <motion.div
            className="fixed inset-0 z-40 bg-black/40"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }}
            onClick={onClose}
          />

          {/* Bottom Sheet */}
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="mobile-transcript-sheet-title"
            className="fixed bottom-nav-sheet left-0 right-0 z-50 h-[70dvh] bg-bg-primary rounded-t-xl border-t border-border shadow-lg flex flex-col"
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }}
            drag="y"
            dragConstraints={{ top: 0 }}
            dragElastic={{ top: 0, bottom: 0.4 }}
            dragMomentum={false}
            onDragEnd={(_, info) => {
              if (info.offset.y > 80 || info.velocity.y > 300) onClose()
            }}
          >
            {/* Drag Handle */}
            <div className="flex justify-center pt-3 pb-1 shrink-0">
              <div className="w-8 h-1 rounded-full bg-border" />
            </div>

            {/* Title Bar */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-border shrink-0">
              <span id="mobile-transcript-sheet-title" className="text-sm font-semibold text-text-primary">逐字稿</span>
              <IconButton label="關閉逐字稿" size="sm" onClick={onClose}>
                <X size={16} />
              </IconButton>
            </div>

            {/* Transcript Content */}
            <div className="flex-1 overflow-hidden">
              <TranscriptPanel
                cues={cues}
                activeCueIdx={activeCueIdx}
                onWordClick={onWordClick}
                hideHeader
              />
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
