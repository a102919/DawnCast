import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { motion, AnimatePresence, useMotionValue } from 'framer-motion'
import { X, Check, BookOpen, CalendarCheck, Repeat, RotateCw } from 'lucide-react'
import { useVocab } from '../state'
import type { VocabItem } from '../api/types'
import { EmptyState } from '../components/primitives/EmptyState'
import { Chip } from '../components/primitives/Chip'
import { ClozeCard } from '../components/flashcard/ClozeCard'
import { ReplayAudioButton } from '../components/flashcard/ReplayAudioButton'
import { SessionHeader } from '../components/flashcard/SessionHeader'
import { SessionShell } from '../components/flashcard/SessionShell'
import { SessionSummary } from '../components/flashcard/SessionSummary'
import { SwipeCard } from '../components/flashcard/SwipeCard'
import type { SwipeDirection, SwipeExit } from '../lib/swipe'
import { MnemonicHint } from '../components/wordcard/MnemonicHint'
import { PronounceButton } from '../components/wordcard/PronounceButton'
import { useSprings } from '../lib/motion'
import { filterReviewDeck, filterPracticePool, buildCloze, storageGet, storageSet, formatMultiline } from '../lib'

type Phase = 'answer' | 'result'
type Mode = 'recognize' | 'cloze'

const MODE_STORAGE_KEY = 'dawncast:flashcards:mode'

function clozeSentence(item: VocabItem): string {
  return item.sourceSentence || item.exampleEn || ''
}

export function FlashcardRoute() {
  // deck 於內層 mount 凍結，等 Provider 載完再掛載（直接輸入網址進來時的競態）
  const { isLoading } = useVocab()
  if (isLoading) return null
  return <FlashcardSession />
}

function FlashcardSession() {
  const { items, updateCardReview } = useVocab()
  const { gentle, press, reduce: shouldReduceMotion } = useSprings()
  const cardScale = useMotionValue(1)
  const shadowPeak = useMotionValue(0)
  const [searchParams] = useSearchParams()
  const isPractice = searchParams.get('practice') === '1'

  const [deck] = useState<readonly VocabItem[]>(() =>
    isPractice ? filterPracticePool(items) : filterReviewDeck(items),
  )
  const [idx, setIdx] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [results, setResults] = useState<number[]>([])
  const [phase, setPhase] = useState<Phase>('answer')
  const [mode, setMode] = useState<Mode>(() => storageGet<Mode>(MODE_STORAGE_KEY) ?? 'recognize')
  // 飛出動畫參數：state 而非 ref——AnimatePresence 的 custom 在 render 時讀取
  const [exitCustom, setExitCustom] = useState<SwipeExit | null>(null)

  const current = deck[idx]
  const sentence = current ? clozeSentence(current) : ''
  const canCloze = current ? buildCloze(sentence, current.word) !== null : false
  const effectiveMode: Mode = mode === 'cloze' && canCloze ? 'cloze' : 'recognize'

  const changeMode = (next: Mode) => {
    setMode(next)
    storageSet(MODE_STORAGE_KEY, next)
  }

  const answer = (quality: number) => {
    if (!current) return
    // 樂觀更新：先前進畫面給使用者回饋，背景同步失敗時整批撤回並提示重試，
    // 否則卡片已翻頁、評分已記，伺服器沒收到會讓 SRS 排程漂掉。
    const capturedIdx = idx
    setResults(r => [...r, quality])
    setFlipped(false)
    const next = idx + 1
    setIdx(next)
    if (next >= deck.length) setPhase('result')
    if (isPractice) return // 自由練習：只走本機進度，不寫回 SRS 排程
    void updateCardReview(current.id, quality).catch((err: unknown) => {
      setIdx(capturedIdx)
      setResults(r => r.slice(0, -1))
      setPhase('answer')
      window.alert(
        `同步評分失敗（${err instanceof Error ? err.message : '未知錯誤'}），已退回本卡，請重試`,
      )
    })
  }

  // 二元滑卡 → SM-2：右滑=q4（記得）、左滑=q1（忘記）；按鈕與鍵盤走同一條路
  const handleSwipe = (dir: SwipeDirection, velocity: number) => {
    setExitCustom({ dir, velocity, reduce: shouldReduceMotion })
    answer(dir === 'right' ? 4 : 1)
  }
  const handleSwipeRef = useRef(handleSwipe)
  useEffect(() => {
    handleSwipeRef.current = handleSwipe
  })

  useEffect(() => {
    if (phase !== 'answer') return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') handleSwipeRef.current('right', 0)
      if (e.key === 'ArrowLeft') handleSwipeRef.current('left', 0)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [phase])

  if (items.length === 0) {
    return (
      <SessionShell>
        <SessionHeader status="" />
        <EmptyState
          icon={BookOpen}
          title="單字本是空的"
          description="先到播放頁收錄幾個單字，再來這裡練習"
          action={{ label: '去播放頁收錄', to: '/player' }}
        />
      </SessionShell>
    )
  }

  if (deck.length === 0) {
    if (isPractice) {
      return (
        <SessionShell>
          <SessionHeader status="" />
          <EmptyState icon={Repeat} title="還沒有進入複習中的字" description="先完成學習新字，累積複習中的單字庫再來練習" />
        </SessionShell>
      )
    }
    const practicePool = filterPracticePool(items)
    return (
      <SessionShell>
        <SessionHeader status="" />
        <EmptyState
          icon={CalendarCheck}
          title="今天沒有到期的卡片"
          description="表現很好！明天繼續複習"
          action={practicePool.length > 0 ? { label: '自由練習', to: '/flashcards?practice=1', variant: 'link' } : undefined}
        />
      </SessionShell>
    )
  }

  const forgotten = results.filter(q => q < 3).length
  const remembered = results.filter(q => q >= 3).length

  return (
    <SessionShell>
      <SessionHeader
        status={
          phase === 'result'
            ? isPractice ? '自由練習完成' : '今日複習完成'
            : `${isPractice ? '自由練習・' : ''}第 ${idx + 1} / ${deck.length} 張`
        }
        progress={phase === 'answer' ? idx / deck.length : undefined}
      />

      {phase === 'answer' && (
        <div className="mb-3">
          <div className="inline-flex gap-1 p-1 rounded-pill bg-bg-canvas">
            <Chip
              active={effectiveMode === 'recognize'}
              onClick={() => changeMode('recognize')}
              className={effectiveMode === 'recognize' ? 'shadow-sm' : ''}
            >
              辨識
            </Chip>
            <Chip
              active={effectiveMode === 'cloze'}
              onClick={() => canCloze && changeMode('cloze')}
              className={`${effectiveMode === 'cloze' ? 'shadow-sm' : ''} ${canCloze ? '' : 'opacity-40 cursor-not-allowed'}`}
            >
              拼字
            </Chip>
          </div>
          {!canCloze && (
            <p className="mt-1.5 text-caption tracking-caption leading-caption text-text-tertiary whitespace-nowrap">
              此卡無例句可挖空，僅能用辨識模式
            </p>
          )}
        </div>
      )}

      {phase === 'result' ? (
        <SessionSummary
          title={forgotten === 0 ? '全部記得！太強了' : '本輪複習完成'}
          stats={[
            { label: '不記得', value: forgotten, tone: 'danger' },
            { label: '記得', value: remembered, tone: 'success' },
          ]}
        >
          {isPractice ? (
            <p className="text-body tracking-body leading-body text-text-secondary">練習不影響複習排程</p>
          ) : forgotten > 0 ? (
            <p className="text-body tracking-body leading-body text-text-secondary">{forgotten} 個忘記的明天再複習</p>
          ) : null}
        </SessionSummary>
      ) : current && effectiveMode === 'cloze' ? (
        <div className="flex-1 min-h-0 overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={`cloze-${idx}`}
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -24 }}
              transition={gentle}
            >
              <ClozeCard key={current.id} item={current} sentence={sentence} onGraded={answer} />
            </motion.div>
          </AnimatePresence>
        </div>
      ) : current ? (
        <div className="flex-1 min-h-0 flex flex-col">
          <div className="relative flex-1 min-h-0">
            {/* 下一張卡底層預覽：只給卡形不洩題，當前卡飛出時新卡從這個位置放大進場 */}
            {idx + 1 < deck.length && (
              <div
                aria-hidden
                className="absolute inset-0 scale-[0.96] translate-y-2 rounded-xl border border-border/30 material-regular shadow-md pointer-events-none"
              />
            )}
            <AnimatePresence custom={exitCustom} initial={false} mode="popLayout">
              <SwipeCard key={current.id} onSwipe={handleSwipe} leftLabel="不記得" rightLabel="記得">
                {/* 用 motion.div+role="button" 而非 <button>：背面放了 ReplayAudioButton/MnemonicHint
                    兩個真的互動元件，HTML 不允許 button 巢狀 button。 */}
                <motion.div
                  role="button"
                  tabIndex={0}
                  onClick={() => setFlipped(f => !f)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setFlipped(f => !f)
                    }
                  }}
                  aria-label={flipped ? '顯示單字面' : '顯示翻譯面'}
                  className="relative block w-full h-full text-left [perspective:1600px] rounded-xl cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                >
                  {/* 峰值陰影層：不參與 3D 旋轉，翻轉經過 90° 瞬間淡入淡出，模擬卡片離開桌面的光影變化 */}
                  {!shouldReduceMotion && (
                    <motion.div
                      aria-hidden
                      className="absolute inset-0 rounded-xl shadow-lg pointer-events-none"
                      style={{ opacity: shadowPeak }}
                    />
                  )}

                  <motion.div
                    className="relative w-full h-full [transform-style:preserve-3d]"
                    style={shouldReduceMotion ? undefined : { scale: cardScale }}
                    animate={{ rotateY: flipped ? 180 : 0 }}
                    transition={gentle}
                    onUpdate={
                      shouldReduceMotion
                        ? undefined
                        : latest => {
                            const angle = typeof latest.rotateY === 'number' ? latest.rotateY : flipped ? 180 : 0
                            const t = Math.sin((angle * Math.PI) / 180)
                            cardScale.set(1 - 0.05 * t)
                            shadowPeak.set(t)
                          }
                    }
                  >
                    <div className="absolute inset-0 [backface-visibility:hidden] rounded-xl border border-border/30 material-regular shadow-md p-7 text-center flex flex-col items-center justify-center hover:border-accent/40 transition-colors duration-fast ease-apple">
                      <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase mb-3">單字</p>
                      <div className="flex items-center justify-center gap-2">
                        <p className="text-display tracking-display leading-display font-bold text-text-primary break-all">{current.word}</p>
                        <PronounceButton audioUrl={null} text={current.word} size={20} label="播放單字發音" />
                      </div>
                      {current.ipa && (
                        <p className="text-body tracking-body leading-body text-text-tertiary font-mono mt-2">{current.ipa}</p>
                      )}
                      <p className="inline-flex items-center gap-1 mt-6 text-caption tracking-caption leading-caption text-text-tertiary">
                        <RotateCw size={12} />
                        點擊翻面・左右滑動評分
                      </p>
                    </div>

                    <div className="absolute inset-0 [backface-visibility:hidden] [transform:rotateY(180deg)] rounded-xl border border-border/30 material-regular shadow-md p-7 text-left space-y-4 overflow-y-auto hover:border-accent/40 transition-colors duration-fast ease-apple">
                      <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase">翻譯</p>
                      <p className="text-title tracking-title leading-title font-semibold text-text-primary break-words whitespace-pre-line">
                        {formatMultiline(current.translation)}
                      </p>
                      <p className="text-body tracking-body leading-body text-text-secondary break-all">
                        <span className="text-text-primary font-medium">{current.word}</span>
                        {current.ipa && <span className="text-text-tertiary font-mono"> {current.ipa}</span>}
                      </p>
                      {current.sourceSentence && (
                        <div className="mt-2 border-t border-border pt-3 space-y-2">
                          <p className="text-caption tracking-caption leading-caption text-text-tertiary italic">{current.sourceSentence}</p>
                          <p className="text-caption tracking-caption leading-caption text-text-tertiary">
                            來自《{current.sourceEpisodeId}》
                          </p>
                          {current.sourceEpisodeId && (
                            <div onClick={e => e.stopPropagation()} className="inline-block">
                              <ReplayAudioButton
                                episodeSlug={current.sourceEpisodeId}
                                timestamp={current.sourceTimestamp}
                                lineNo={current.sourceLineNo}
                              />
                            </div>
                          )}
                        </div>
                      )}
                      {current.mnemonic && (
                        <div onClick={e => e.stopPropagation()} className="pt-2">
                          <MnemonicHint text={current.mnemonic} />
                        </div>
                      )}
                    </div>
                  </motion.div>
                </motion.div>
              </SwipeCard>
            </AnimatePresence>
          </div>

          {/* 無障礙 fallback：與滑卡走同一條 commit 路徑；鍵盤 ←/→ 亦同 */}
          <div className="mt-4 grid grid-cols-2 gap-3">
            <motion.button
              onClick={() => handleSwipe('left', 0)}
              whileTap={{ scale: 0.94 }}
              transition={press}
              className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-warning/10 text-warning shadow-sm hover:bg-warning/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <X size={18} />
              <span className="text-caption tracking-caption leading-caption font-medium">不記得</span>
            </motion.button>
            <motion.button
              onClick={() => handleSwipe('right', 0)}
              whileTap={{ scale: 0.94 }}
              transition={press}
              className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-success/10 text-success shadow-sm hover:bg-success/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <Check size={18} />
              <span className="text-caption tracking-caption leading-caption font-medium">記得</span>
            </motion.button>
          </div>

          <div className="flex items-center justify-center gap-4 text-caption tracking-caption leading-caption text-text-tertiary pt-2">
            <span className="text-warning">不記得 {forgotten}</span>
            <span>·</span>
            <span className="text-success">記得 {remembered}</span>
          </div>
        </div>
      ) : null}
    </SessionShell>
  )
}
