import { useState, useMemo } from 'react'
import { AnimatePresence } from 'framer-motion'
import { X, Search, BookOpen, SearchX } from 'lucide-react'
import { useVocab, usePlayer } from '../../state'
import { IconButton, Chip, Sheet, EmptyState } from '../primitives'
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

  const handleSeek = (item: VocabItem) => {
    seekTo(item.sourceTimestamp)
    onClose()
  }

  return (
    <Sheet isOpen={isOpen} onClose={onClose} variant="side" ariaLabelledBy="vocab-drawer-title">
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
      <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-4">
        {filtered.length === 0 ? (
          items.length === 0 ? (
            <EmptyState icon={BookOpen} size="compact" title="單字本是空的" description="點擊字幕中的單字即可收錄" />
          ) : (
            <EmptyState icon={SearchX} size="compact" title="找不到符合的單字" />
          )
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
    </Sheet>
  )
}
