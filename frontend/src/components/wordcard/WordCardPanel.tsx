import { useEffect, type ReactNode } from 'react'
import { X, BookmarkPlus, Check, AlertCircle, RotateCcw, Play } from 'lucide-react'
import { AnimatePresence, motion } from 'framer-motion'
import { toast } from 'sonner'
import type { DictEntry } from '../../api/types'
import type { Cue } from '../../types/episode'
import { useVocab } from '../../state'
import { formatPos, formatExchange, formatTimestamp } from '../../lib'
import { IconButton } from '../primitives'
import { PronounceButton } from './PronounceButton'

function highlightWord(sentence: string, word: string): ReactNode {
  if (!word) return sentence
  const lower = sentence.toLowerCase()
  const lowerWord = word.toLowerCase()
  const idx = lower.indexOf(lowerWord)
  if (idx === -1) return sentence
  return (
    <>
      {sentence.slice(0, idx)}
      <span className="font-semibold text-accent">{sentence.slice(idx, idx + word.length)}</span>
      {sentence.slice(idx + word.length)}
    </>
  )
}

interface WordCardPanelProps {
  readonly isOpen: boolean
  readonly word: string | null
  readonly entry: DictEntry | null
  readonly lookupError: string | null
  readonly onRetry: () => void
  readonly activeCue: Cue | null
  readonly episodeId: string
  readonly activeCueIdx: number
  readonly onClose: () => void
  readonly onReplayCue: () => void
}

export function WordCardPanel({ isOpen, word, entry, lookupError, onRetry, activeCue, episodeId, activeCueIdx, onClose, onReplayCue }: WordCardPanelProps) {
  const { addVocab, isInVocab } = useVocab()
  const inVocab = word ? isInVocab(word) : false

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  const handleAddVocab = async () => {
    if (!word || !entry || !activeCue) return
    try {
      await addVocab({
        word,
        lemma: entry.word,
        pos: entry.pos[0],
        translation: entry.translation,
        ipa: entry.ipa,
        sourceEpisodeId: episodeId,
        sourceLineNo: activeCueIdx,
        sourceTimestamp: activeCue.start,
        sourceSentence: activeCue.text,
        sourceSentenceZh: activeCue.zh,
        senseIdx: 0,
      })
    } catch {
      toast.error('加入單字本失敗，請重試')
    }
  }

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* 點外面關閉 */}
          <div className="fixed inset-0 z-40" onClick={onClose} />

          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="word-card-panel-title"
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }}
            className="fixed bottom-nav-sheet left-0 right-0 z-50 bg-bg-primary border-t border-border shadow-lg rounded-t-xl max-h-[90vh]"
            drag="y"
            dragConstraints={{ top: 0 }}
            dragElastic={{ top: 0, bottom: 0.4 }}
            dragMomentum={false}
            onDragEnd={(_, info) => {
              if (info.offset.y > 80 || info.velocity.y > 300) onClose()
            }}
          >
            {/* drag handle */}
            <div className="flex justify-center pt-3 pb-1">
              <div className="w-8 h-1 rounded-full bg-border" />
            </div>

            <div className="px-5 pb-6 overflow-y-auto max-h-[calc(90vh-40px)]">
              {/* 標題列 */}
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 id="word-card-panel-title" className="text-2xl font-semibold text-text-primary">
                    {word ?? '—'}
                  </h3>
                  {entry && (
                    <div className="flex items-center gap-2 mt-0.5">
                      <PronounceButton audioUrl={entry.audioUrl} text={word} />
                      {entry.ipa && (
                        <span className="text-xs text-text-tertiary font-mono">{entry.ipa}</span>
                      )}
                      {entry.pos.length > 0 && (
                        <span className="text-xs text-text-tertiary">{formatPos(entry.pos)}</span>
                      )}
                    </div>
                  )}
                  {!entry && word && !lookupError && (
                    <div className="flex items-center gap-2 mt-0.5 animate-pulse">
                      <div className="h-3.5 bg-bg-secondary rounded w-20" />
                      <div className="h-3.5 bg-bg-secondary rounded w-10" />
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 mt-1">
                  <button
                    onClick={handleAddVocab}
                    disabled={inVocab || !entry}
                    className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                      inVocab
                        ? 'bg-success/10 text-success cursor-default'
                        : !entry
                          ? 'bg-bg-secondary text-text-tertiary cursor-default'
                          : 'bg-accent text-white hover:bg-accent-hover cursor-pointer'
                    }`}
                  >
                    {inVocab ? (
                      <><Check size={14} /> 已收錄</>
                    ) : (
                      <><BookmarkPlus size={14} /> 加入單字本</>
                    )}
                  </button>
                  <IconButton label="關閉詞卡" onClick={onClose}>
                    <X size={18} />
                  </IconButton>
                </div>
              </div>

              <hr className="border-border mb-3" />

              {/* 內容 */}
              {lookupError ? (
                <div className="flex flex-col items-center gap-2 py-3">
                  <AlertCircle size={20} className="text-danger" />
                  <p className="text-sm text-danger">{lookupError}</p>
                  <button
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-text-secondary bg-bg-secondary hover:bg-border rounded-md transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                    onClick={onRetry}
                  >
                    <RotateCcw size={13} />
                    重試
                  </button>
                </div>
              ) : !entry && word && !lookupError ? (
                <div className="space-y-2.5 animate-pulse">
                  {/* 翻譯 skeleton */}
                  <div className="h-6 bg-bg-secondary rounded w-3/4" />
                  {/* IPA + 詞性 skeleton */}
                  <div className="flex gap-2">
                    <div className="h-4 bg-bg-secondary rounded w-24" />
                    <div className="h-4 bg-bg-secondary rounded w-12" />
                  </div>
                  {/* 來源 skeleton */}
                  <div className="h-3 bg-bg-secondary rounded w-40 mt-1" />
                </div>
              ) : !entry ? (
                <p className="text-text-secondary text-sm">找不到釋義</p>
              ) : (
                <div className="space-y-3">
                  <p className="text-xl font-medium text-text-primary whitespace-pre-line">{entry.translation.replaceAll('\\n', '\n')}</p>
                  {entry.exchange && (
                    <p className="text-xs text-text-tertiary">{formatExchange(entry.exchange)}</p>
                  )}
                  {(entry.exampleEn || entry.exampleZh) && (
                    <div className="border-l-2 border-border pl-3 py-1 space-y-1">
                      {entry.exampleEn && (
                        <p className="text-sm leading-relaxed text-text-primary flex items-start gap-1.5">
                          <span>{entry.exampleEn}</span>
                          <PronounceButton audioUrl={null} text={entry.exampleEn} size={12} label="播放例句發音" />
                        </p>
                      )}
                      {entry.exampleZh && (
                        <p className="text-sm leading-relaxed text-text-secondary">
                          {entry.exampleZh}
                        </p>
                      )}
                    </div>
                  )}
                  {activeCue && (entry.exampleEn || entry.exampleZh) && (
                    <div className="mt-1 space-y-2">
                      {/* P0-3：原始語境例句提升權重，搬移到翻譯下方、IPH 之前；用左邊框凸顯、不包灰底 */}
                      {/* ponytail: 只在字典真的有例句時才附上 podcast 語境句，避免拿 podcast 原句冒充字典例句 */}
                      <div className="border-l-2 border-accent pl-3 py-1">
                        <p className="text-base leading-relaxed text-text-primary">
                          {highlightWord(activeCue.text, word ?? '')}
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={onReplayCue}
                        className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:text-accent-hover transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                      >
                        <Play size={12} fill="currentColor" />
                        重聽這句
                      </button>
                      <p className="text-xs text-text-tertiary">
                        來自 {formatTimestamp(activeCue.start)} · {activeCue.speaker}
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
