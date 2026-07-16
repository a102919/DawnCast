import { useState, useMemo, useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { X, Search, BookOpen, SearchX } from 'lucide-react'
import { useVocab, usePlayer } from '../../state'
import { IconButton, Chip } from '../primitives'
import { VocabEntryCard } from './VocabEntryCard'
import type { VocabItem } from '../../api/types'

interface VocabDrawerProps {
  readonly isOpen: boolean
  readonly onClose: () => void
}

type PosFilter = 'all' | 'v' | 'n' | 'a'

const POS_LABELS: Record<PosFilter, string> = {
  all: '全部',
  v: '動詞',
  n: '名詞',
  a: '形容詞',
} as const

export function VocabDrawer({ isOpen, onClose }: VocabDrawerProps) {
  const { items, removeVocab } = useVocab()
  const { seekTo } = usePlayer()
  const [query, setQuery] = useState('')
  const [posFilter, setPosFilter] = useState<PosFilter>('all')

  const filtered = useMemo(() => {
    let result = [...items]
    if (query.trim()) {
      const q = query.toLowerCase()
      result = result.filter(
        v => v.word.toLowerCase().includes(q) || v.translation.includes(q)
      )
    }
    if (posFilter !== 'all') {
      result = result.filter(v => v.pos?.startsWith(posFilter))
    }
    return result
  }, [items, query, posFilter])

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  const handleSeek = (item: VocabItem) => {
    seekTo(item.sourceTimestamp)
    onClose()
  }

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 bg-black/20 z-40"
            onClick={onClose}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby="vocab-drawer-title"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ duration: 0.24, ease: [0.2, 0.8, 0.2, 1] }}
            className="fixed top-0 right-0 h-full w-96 max-w-full bg-bg-primary shadow-lg z-50 flex flex-col"
          >
            {/* 標頭 */}
            <div className="flex items-center justify-between px-5 h-14 border-b border-border shrink-0">
              <h2 id="vocab-drawer-title" className="text-base font-semibold text-text-primary">
                單字本 ({items.length})
              </h2>
              <IconButton label="關閉" onClick={onClose}>
                <X size={18} />
              </IconButton>
            </div>

            {/* 搜尋框 */}
            <div className="px-4 pt-3 pb-2 shrink-0">
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary" />
                <input
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="搜尋單字或翻譯..."
                  className="w-full pl-8 pr-3 py-2 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-accent transition-colors duration-fast"
                />
              </div>
            </div>

            {/* 詞性篩選 */}
            <div className="px-4 pb-3 flex gap-1.5 shrink-0">
              {(Object.keys(POS_LABELS) as PosFilter[]).map(key => (
                <Chip
                  key={key}
                  active={posFilter === key}
                  onClick={() => setPosFilter(key)}
                >
                  {POS_LABELS[key]}
                </Chip>
              ))}
            </div>

            {/* 列表 */}
            <div className="flex-1 overflow-y-auto px-3 pb-4">
              {filtered.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-bg-secondary flex items-center justify-center text-text-tertiary">
                    {items.length === 0 ? <BookOpen size={22} /> : <SearchX size={22} />}
                  </div>
                  <div className="text-text-secondary text-sm">
                    {items.length === 0 ? (
                      <>
                        <p className="font-medium text-text-primary mb-1">單字本是空的</p>
                        <p>點擊字幕中的單字即可收錄</p>
                      </>
                    ) : (
                      '找不到符合的單字'
                    )}
                  </div>
                </div>
              ) : (
                <div className="space-y-2">
                  <AnimatePresence mode="popLayout" initial={false}>
                    {filtered.map(item => (
                      <VocabEntryCard
                        key={item.id}
                        item={item}
                        onSeek={handleSeek}
                        onRemove={removeVocab}
                        variant="drawer"
                      />
                    ))}
                  </AnimatePresence>
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}

