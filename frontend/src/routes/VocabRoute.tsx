import { useState, useMemo } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Search, BookOpen, SearchX, Sparkles } from 'lucide-react'
import { useNavigate, Link } from 'react-router-dom'
import { useVocab, usePlayer } from '../state'
import { Chip } from '../components/primitives/Chip'
import { VocabEntryCard } from '../components/vocab/VocabEntryCard'
import type { VocabItem } from '../api/types'

type PosFilter = 'all' | 'v' | 'n' | 'a'

const POS_LABELS: Record<PosFilter, string> = {
  all: '全部',
  v: '動詞',
  n: '名詞',
  a: '形容詞',
} as const

export function VocabRoute() {
  const { items, isLoading, removeVocab } = useVocab()
  const { seekTo } = usePlayer()
  const navigate = useNavigate()
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
    navigate('/player')
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">

      {/* 標頭 */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-text-primary">單字本</h1>
          <p className="text-sm text-text-secondary mt-0.5">
            共 {items.length} 個單字
          </p>
        </div>
      </div>

      {/* 閃卡複習入口 */}
      {items.length > 0 && (
        <Link
          to="/flashcards"
          className="mb-4 flex items-center justify-between gap-2 p-3 rounded-lg bg-accent/10 border border-accent/30 hover:bg-accent/15 transition-colors duration-fast group"
        >
          <div className="flex items-center gap-2 min-w-0">
            <Sparkles size={16} className="text-accent shrink-0" />
            <span className="text-sm font-medium text-text-primary">開始閃卡複習</span>
            <span className="text-xs text-text-tertiary">（{items.length} 張）</span>
          </div>
          <span className="text-xs text-accent font-medium shrink-0 group-hover:underline">前往 →</span>
        </Link>
      )}

      {/* 搜尋 */}
      <div className="relative mb-3">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary" />
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="搜尋單字或翻譯..."
          className="w-full pl-8 pr-3 py-2.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-accent transition-colors duration-fast"
        />
      </div>

      {/* 詞性篩選 */}
      <div className="flex gap-1.5 mb-4">
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
      {isLoading ? null : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center gap-3">
          <div className="w-12 h-12 rounded-full bg-bg-secondary flex items-center justify-center text-text-tertiary">
            {items.length === 0 ? <BookOpen size={22} /> : <SearchX size={22} />}
          </div>
          <div className="text-text-secondary text-sm">
            {items.length === 0 ? (
              <>
                <p className="font-medium text-text-primary mb-1">單字本是空的</p>
                <p>在播放頁點擊字幕中的單字即可收錄</p>
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
                variant="page"
              />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}

