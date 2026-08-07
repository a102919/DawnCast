import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { toast } from 'sonner'
import { Play, Trash2, ExternalLink, RotateCcw } from 'lucide-react'
import { formatTimestamp, formatPos, formatMultiline, MASTERED_STATUS } from '../../lib'
import { api } from '../../api'
import type { DictEntry, VocabItem } from '../../api/types'
import type { Cue } from '../../types/episode'
import { PronounceButton } from '../wordcard/PronounceButton'
import { WordCardPanel } from '../wordcard/WordCardPanel'

export interface VocabEntryCardProps {
  readonly item: VocabItem
  readonly onSeek: (item: VocabItem) => void
  readonly onRemove: (id: string) => Promise<void>
  /** 已精熟的字提供時顯示「重新複習」鈕（一鍵復活回 SRS 佇列） */
  readonly onRevive?: (id: string) => void
  /** 'page'：單字本頁面（較寬鬆版面）；'drawer'：側邊抽屜（緊湊版面） */
  readonly variant?: 'page' | 'drawer'
}

const cardVariants = {
  initial: { opacity: 0, height: 0, marginTop: 0 },
  animate: { opacity: 1, height: 'auto', marginTop: 0, transition: { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] } },
  exit: { opacity: 0, height: 0, marginTop: 0, transition: { duration: 0.2, ease: [0.2, 0.8, 0.2, 1] } },
} as const

export function VocabEntryCard({ item, onSeek, onRemove, onRevive, variant = 'page' }: VocabEntryCardProps) {
  const isDrawer = variant === 'drawer'
  // ponytail: 來源集數的 titleZh 暫不在 VocabItem 上，目前顯示 slug 短碼讓使用者知道來源是哪一集；
  // 等 VocabItem 加 sourceEpisodeTitle 欄位後再換成中文標題。
  const sourceLabel = item.sourceEpisodeId
    ? `E${item.sourceEpisodeId.replace(/^episode_/, '').slice(0, 6)}`
    : null
  const [isPanelOpen, setIsPanelOpen] = useState(false)
  const [dictEntry, setDictEntry] = useState<DictEntry | null>(null)
  const [lookupError, setLookupError] = useState<string | null>(null)

  const loadEntry = useCallback((isCancelled: () => boolean) => {
    setLookupError(null)
    api.lookupDict(item.word)
      .then(entry => { if (!isCancelled()) setDictEntry(entry) })
      .catch(() => { if (!isCancelled()) setLookupError('查詢失敗，請重試') })
  }, [item.word])

  useEffect(() => {
    let cancelled = false
    // 非同步查字典模式：setLookupError(null) 是同步重置，之後的 setState 都在
    // promise 之後才發生，不會造成 render 迴圈；規則誤報，抑制之。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadEntry(() => cancelled)
    return () => { cancelled = true }
  }, [loadEntry])

  // 卡片點擊開啟的詞卡（WordCardPanel）沿用播放頁的同一元件，需要一個 Cue 形狀的來源句
  const cueForPanel: Cue = {
    index: item.sourceLineNo,
    speaker: '',
    text: item.sourceSentence ?? '',
    zh: item.sourceSentenceZh ?? '',
    start: item.sourceTimestamp,
    end: item.sourceTimestamp,
  }

  return (
    <>
      <motion.div
        variants={cardVariants}
        initial="initial"
        animate="animate"
        exit="exit"
        style={{ overflow: 'hidden' }}
        layout
      >
        <div
          role="button"
          tabIndex={0}
          onClick={() => setIsPanelOpen(true)}
          onKeyDown={e => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setIsPanelOpen(true) }
          }}
          className={[
            'border border-border hover:border-accent/30 transition-[border-color,transform] duration-fast active:scale-[0.99] cursor-pointer',
            isDrawer
              ? 'group p-3 rounded-md bg-bg-secondary'
              : 'p-4 rounded-lg bg-bg-primary',
          ].join(' ')}
        >
          <div className={`flex items-start justify-between ${isDrawer ? 'gap-2' : 'gap-3'}`}>
            <div className={isDrawer ? 'min-w-0' : 'min-w-0 flex-1'}>
              {isDrawer ? (
                <>
                  <div className="font-medium text-text-primary">{item.word}</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <PronounceButton audioUrl={dictEntry?.audioUrl} text={item.word} size={12} />
                    {item.ipa && (
                      <span className="text-xs font-mono text-text-tertiary">{item.ipa}</span>
                    )}
                    {item.pos && (
                      <span className="text-xs text-text-tertiary">{formatPos([item.pos])}</span>
                    )}
                  </div>
                </>
              ) : (
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="font-semibold text-text-primary">{item.word}</span>
                  <PronounceButton audioUrl={dictEntry?.audioUrl} text={item.word} size={12} />
                  {item.ipa && (
                    <span className="text-xs font-mono text-text-tertiary">{item.ipa}</span>
                  )}
                  {item.pos && (
                    <span className="text-xs text-text-tertiary bg-bg-secondary px-1.5 py-0.5 rounded">
                      {formatPos([item.pos])}
                    </span>
                  )}
                </div>
              )}
              {item.senses && item.senses.length > 0 ? (
                <div className="flex items-baseline gap-1.5 mt-1">
                  {item.senses[0].pos && (
                    <span className="shrink-0 text-xs font-mono text-text-tertiary border border-border px-1.5 py-0.5 rounded">
                      {item.senses[0].pos}
                    </span>
                  )}
                  <span className="text-sm text-text-secondary">{item.senses[0].zh}</span>
                </div>
              ) : (
                <div className="text-sm text-text-secondary mt-1 whitespace-pre-line">{formatMultiline(item.translation)}</div>
              )}

              {/* 來源行 */}
              {isDrawer ? (
                <div className="text-xs text-text-tertiary mt-1">
                  {formatTimestamp(item.sourceTimestamp)}
                </div>
              ) : (
                <button
                  onClick={e => { e.stopPropagation(); onSeek(item) }}
                  className="mt-1.5 inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-accent transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                >
                  <ExternalLink size={10} />
                  {sourceLabel
                    ? `${sourceLabel} · ${formatTimestamp(item.sourceTimestamp)}`
                    : formatTimestamp(item.sourceTimestamp)}
                </button>
              )}
            </div>

            <div className={`flex items-start shrink-0 ${isDrawer ? 'flex-col gap-1' : 'gap-0'}`}>
              {onRevive && item.status === MASTERED_STATUS && (
                <button
                  aria-label="重新加入複習"
                  onClick={e => { e.stopPropagation(); onRevive(item.id) }}
                  className="inline-flex items-center justify-center gap-1 px-2 py-1.5 min-h-[44px] text-xs text-accent hover:bg-accent/10 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-md"
                >
                  <RotateCcw size={11} />
                  重新複習
                </button>
              )}
              {isDrawer && (
                <button
                  onClick={e => { e.stopPropagation(); onSeek(item) }}
                  className="inline-flex items-center justify-center gap-1 px-2 py-1.5 min-h-[44px] min-w-[44px] text-xs text-accent hover:bg-accent/10 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                >
                  <Play size={11} />
                  跳到
                </button>
              )}
              <button
                aria-label="移除單字"
                onClick={e => {
                  e.stopPropagation()
                  void onRemove(item.id).catch(() => toast.error('移除失敗，請重試'))
                }}
                className={`inline-flex items-center justify-center px-2.5 py-1.5 min-h-[44px] min-w-[44px] text-xs text-text-tertiary hover:text-danger hover:bg-danger/10 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${isDrawer ? 'px-2 rounded' : 'rounded-md'}`}
              >
                <Trash2 size={isDrawer ? 14 : 11} />
              </button>
            </div>
          </div>
        </div>
      </motion.div>

      {/* 詞卡：與播放頁點單字彈出的 WordCardPanel 共用同一元件 */}
      <WordCardPanel
        isOpen={isPanelOpen}
        word={item.word}
        entry={dictEntry}
        lookupError={lookupError}
        onRetry={() => loadEntry(() => false)}
        activeCue={cueForPanel}
        episodeId={item.sourceEpisodeId}
        activeCueIdx={item.sourceLineNo}
        onClose={() => setIsPanelOpen(false)}
        onGoToSource={() => { onSeek(item); setIsPanelOpen(false) }}
      />
    </>
  )
}
