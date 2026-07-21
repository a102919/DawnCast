import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { Sparkles, ArrowLeft, Check, X as XIcon, BookOpen, CalendarCheck } from 'lucide-react'
import { useVocab } from '../state'
import type { VocabItem } from '../api/types'
import { EmptyState } from '../components/primitives/EmptyState'
import { StatCard } from '../components/primitives/StatCard'
import { useSprings } from '../lib/motion'

function buildDeck(items: readonly VocabItem[]): readonly VocabItem[] {
  if (items.length === 0) return []
  const today = new Date().toLocaleDateString('en-CA')
  const due = items.filter(item => !item.nextReview || item.nextReview <= today)
  return [...due].sort((a, b) => {
    if (!a.nextReview && !b.nextReview) return 0
    if (!a.nextReview) return -1
    if (!b.nextReview) return 1
    return a.nextReview.localeCompare(b.nextReview)
  })
}

type Phase = 'answer' | 'result'

export function FlashcardRoute() {
  const { items, updateCardReview } = useVocab()
  const navigate = useNavigate()
  const { gentle } = useSprings()

  const [deck] = useState<readonly VocabItem[]>(() => buildDeck(items))
  const [idx, setIdx] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [known, setKnown] = useState(0)
  const [unknown, setUnknown] = useState(0)
  const [phase, setPhase] = useState<Phase>('answer')

  const current = deck[idx]

  const answer = (gotIt: boolean) => {
    if (!current) return
    void updateCardReview(current.id, gotIt ? 4 : 1)
    if (gotIt) setKnown(k => k + 1)
    else setUnknown(u => u + 1)
    setFlipped(false)
    const next = idx + 1
    setIdx(next)
    if (next >= deck.length) setPhase('result')
  }

  if (items.length === 0) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-6">
        <button
          onClick={() => navigate('/vocab')}
          className="inline-flex items-center gap-1 text-sm text-text-tertiary hover:text-text-secondary mb-4 transition-colors duration-fast"
        >
          <ArrowLeft size={14} />
          回到單字本
        </button>
        <EmptyState
          icon={BookOpen}
          title="單字本是空的"
          description="先到播放頁收錄幾個單字，再來這裡練習"
          action={{ label: '去播放頁收錄', to: '/player' }}
        />
      </div>
    )
  }

  if (deck.length === 0) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-6">
        <button
          onClick={() => navigate('/vocab')}
          className="inline-flex items-center gap-1 text-sm text-text-tertiary hover:text-text-secondary mb-4 transition-colors duration-fast"
        >
          <ArrowLeft size={14} />
          回到單字本
        </button>
        <EmptyState icon={CalendarCheck} title="今天沒有到期的卡片" description="表現很好！明天繼續複習" />
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-4">
        <button
          onClick={() => navigate('/vocab')}
          className="inline-flex items-center gap-1 text-sm text-text-tertiary hover:text-text-secondary transition-colors duration-fast"
        >
          <ArrowLeft size={14} />
          回到單字本
        </button>
        <p className="text-xs text-text-tertiary">
          {phase === 'result' ? '今日複習完成' : `第 ${idx + 1} / ${deck.length} 張`}
        </p>
      </div>

      <AnimatePresence mode="wait">
        {phase === 'result' ? (
          <motion.div
            key="result"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
            className="rounded-xl border border-border bg-bg-primary p-8 text-center space-y-5"
          >
            <div className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-accent/10 text-accent">
              <Sparkles size={24} />
            </div>
            <h2 className="text-xl font-semibold text-text-primary">
              {unknown === 0 ? '全部認識！太強了' : '本輪複習完成'}
            </h2>
            <div className="grid grid-cols-2 gap-3 max-w-xs mx-auto">
              <StatCard label="認識" value={known} tone="success" />
              <StatCard label="不認識" value={unknown} tone="warning" />
            </div>
            {unknown > 0 && (
              <p className="text-sm text-text-secondary">{unknown} 個不認識的明天再複習</p>
            )}
            <button
              onClick={() => navigate('/vocab')}
              className="inline-flex items-center justify-center gap-1.5 px-4 py-2 text-sm font-medium rounded-md bg-bg-secondary text-text-primary hover:bg-border transition-colors duration-fast"
            >
              回到單字本
            </button>
          </motion.div>
        ) : current ? (
          <motion.div
            key={`card-${idx}`}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2 }}
            className="space-y-4"
          >
            <button
              type="button"
              onClick={() => setFlipped(f => !f)}
              aria-label={flipped ? '顯示單字面' : '顯示翻譯面'}
              className="block w-full min-h-[240px] text-left [perspective:1600px] rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <motion.div
                className="relative w-full h-full min-h-[240px] [transform-style:preserve-3d]"
                animate={{ rotateY: flipped ? 180 : 0 }}
                transition={gentle}
              >
                <div className="absolute inset-0 [backface-visibility:hidden] rounded-xl border border-border bg-bg-primary p-10 text-center hover:border-accent/40 transition-colors duration-fast">
                  <p className="text-[10px] font-semibold tracking-wider text-text-tertiary uppercase mb-3">單字</p>
                  <p className="text-3xl font-bold text-text-primary break-all">{current.word}</p>
                  {current.ipa && (
                    <p className="text-sm text-text-tertiary font-mono mt-2">{current.ipa}</p>
                  )}
                  <p className="text-xs text-text-tertiary mt-6">點擊卡片查看翻譯</p>
                </div>

                <div className="absolute inset-0 [backface-visibility:hidden] [transform:rotateY(180deg)] rounded-xl border border-border bg-bg-primary p-10 text-left space-y-3 overflow-y-auto hover:border-accent/40 transition-colors duration-fast">
                  <p className="text-[10px] font-semibold tracking-wider text-text-tertiary uppercase">翻譯</p>
                  <p className="text-2xl font-medium text-text-primary break-words">{current.translation}</p>
                  <p className="text-sm text-text-secondary break-all">
                    <span className="text-text-primary font-medium">{current.word}</span>
                    {current.ipa && <span className="text-text-tertiary font-mono"> {current.ipa}</span>}
                  </p>
                  {current.sourceSentence && (
                    <div className="mt-2 border-t border-border pt-3 space-y-1">
                      <p className="text-xs text-text-tertiary leading-relaxed italic">{current.sourceSentence}</p>
                      <p className="text-[10px] text-text-tertiary">
                        來自《{current.sourceEpisodeId}》
                      </p>
                    </div>
                  )}
                </div>
              </motion.div>
            </button>

            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => answer(false)}
                className="inline-flex items-center justify-center gap-1.5 py-3 rounded-lg bg-bg-secondary text-text-primary hover:bg-border transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <XIcon size={16} />
                不認識
              </button>
              <button
                onClick={() => answer(true)}
                className="inline-flex items-center justify-center gap-1.5 py-3 rounded-lg bg-success text-white hover:bg-success/90 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Check size={16} />
                認識
              </button>
            </div>

            <div className="flex items-center justify-center gap-4 text-xs text-text-tertiary pt-1">
              <span className="text-success">認識 {known}</span>
              <span>·</span>
              <span className="text-warning">不認識 {unknown}</span>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
