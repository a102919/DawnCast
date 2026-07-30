import { useState, useMemo } from 'react'
import { AnimatePresence } from 'framer-motion'
import { toast } from 'sonner'
import { Search, BookOpen, SearchX, WifiOff } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useVocab } from '../state'
import { Chip } from '../components/primitives/Chip'
import { EmptyState } from '../components/primitives/EmptyState'
import { VocabEntryCard } from '../components/vocab/VocabEntryCard'
import { SessionSummaryCard } from '../components/vocab/SessionSummaryCard'
import { StartSessionButton } from '../components/vocab/StartSessionButton'
import { MASTERED_STATUS } from '../lib/srs'
import type { VocabItem } from '../api/types'

type PosFilter = 'all' | 'v' | 'n' | 'a'
type MasteryFilter = 'reviewing' | 'mastered'

const POS_LABELS: Record<PosFilter, string> = {
  all: '全部',
  v: '動詞',
  n: '名詞',
  a: '形容詞',
} as const

const MASTERY_LABELS: Record<MasteryFilter, string> = {
  reviewing: '複習中',
  mastered: '已精熟',
} as const

/** 單字本首頁：標頭 + 主 CTA「開始學習」+ 學習摘要 + 搜尋/篩選/列表。
 *  「開始學習」一個入口取代舊三入口，佇列永遠有得學。 */
export function VocabRoute() {
  const { items, isLoading, error, reload, removeVocab, reviveVocab } = useVocab()
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [posFilter, setPosFilter] = useState<PosFilter>('all')
  const [masteryFilter, setMasteryFilter] = useState<MasteryFilter>('reviewing')

  const filtered = useMemo(() => {
    let result = items.filter(v =>
      masteryFilter === 'mastered'
        ? v.status === MASTERED_STATUS
        : v.status !== MASTERED_STATUS,
    )
    if (query.trim()) {
      const q = query.toLowerCase()
      result = result.filter(
        v => v.word.toLowerCase().includes(q) || v.translation.includes(q),
      )
    }
    if (posFilter !== 'all') {
      result = result.filter(v => v.pos?.startsWith(posFilter))
    }
    return result
  }, [items, query, posFilter, masteryFilter])

  const handleSeek = (item: VocabItem) => {
    // 帶正確集數 id 導頁；seek 由 PlayerRoute 內部處理，不能在這裡直接 seek
    navigate(`/player/${item.sourceEpisodeId}`, {
      state: { seekTo: item.sourceTimestamp, seekLineNo: item.sourceLineNo },
    })
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-6">
        <div className="flex flex-col items-center justify-center text-center gap-3 py-20">
          <div className="w-12 h-12 rounded-full bg-bg-secondary flex items-center justify-center text-text-tertiary">
            <WifiOff size={22} />
          </div>
          <div className="text-text-secondary text-sm">
            <p className="font-medium text-text-primary mb-1">單字本載入失敗</p>
            <p>{error}</p>
          </div>
          <button
            type="button"
            onClick={() => void reload()}
            className="mt-1 px-4 py-2 rounded-md bg-accent text-white text-sm font-medium hover:bg-accent/90 active:scale-[0.97] transition-all duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            重試
          </button>
        </div>
      </div>
    )
  }

  if (isLoading) return null

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-text-primary tracking-tight">單字本</h1>
          <p className="text-sm text-text-secondary mt-0.5">共 {items.length} 個單字</p>
        </div>
      </div>

      <StartSessionButton items={items} />
      <SessionSummaryCard items={items} />

      <div>
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

        <div className="flex gap-1.5 mb-2.5">
          {(Object.keys(MASTERY_LABELS) as MasteryFilter[]).map(key => (
            <Chip key={key} active={masteryFilter === key} onClick={() => setMasteryFilter(key)}>
              {MASTERY_LABELS[key]}
            </Chip>
          ))}
        </div>

        <div className="flex gap-1.5 mb-4">
          {(Object.keys(POS_LABELS) as PosFilter[]).map(key => (
            <Chip key={key} active={posFilter === key} onClick={() => setPosFilter(key)}>
              {POS_LABELS[key]}
            </Chip>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        items.length === 0 ? (
          <EmptyState icon={BookOpen} title="單字本是空的" description="在播放頁點擊字幕中的單字即可收錄" />
        ) : masteryFilter === 'mastered' ? (
          <EmptyState icon={SearchX} title="還沒有已精熟的單字" description="持續複習到解鎖畢業測驗，連續兩輪通過就會出現在這裡" />
        ) : (
          <EmptyState icon={SearchX} title="找不到符合的單字" />
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
                onRevive={id => {
                  void reviveVocab(id).catch((err: unknown) => {
                    toast.error(
                      `重新加入複習失敗（${err instanceof Error ? err.message : '未知錯誤'}），請重試`,
                    )
                  })
                }}
                variant="page"
              />
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  )
}
