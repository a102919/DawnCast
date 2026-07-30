import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { GraduationCap, X, Check } from 'lucide-react'
import { useVocab } from '../state'
import type { VocabItem } from '../api/types'
import { EmptyState } from '../components/primitives/EmptyState'
import { Button } from '../components/primitives/Button'
import { ReplayAudioButton } from '../components/flashcard/ReplayAudioButton'
import { SessionHeader } from '../components/flashcard/SessionHeader'
import { SessionShell } from '../components/flashcard/SessionShell'
import { SessionSummary } from '../components/flashcard/SessionSummary'
import { SwipeCard } from '../components/flashcard/SwipeCard'
import { MnemonicHint } from '../components/wordcard/MnemonicHint'
import { PronounceButton } from '../components/wordcard/PronounceButton'
import { useSprings } from '../lib/motion'
import { filterLearnDeck, formatMultiline, LEARN_SESSION_LIMIT } from '../lib'
import type { SwipeDirection, SwipeExit } from '../lib/swipe'
import { speakWord } from '../lib/speech'

/** 學習模式：新字資訊全展開一次看完，右滑「記住了」進入 SRS（明天首複），
 *  左滑「再看一次」移到佇列尾端（不打 API，中途離開天然斷點續學）。 */
export function LearnRoute() {
  // deck 在內層元件 mount 時凍結；直接輸入網址進來時要等 Provider 載完才掛載，
  // 否則 items 還是 [] 就凍出空 deck，載完也不會回填。
  const { isLoading } = useVocab()
  if (isLoading) return null
  return <LearnSession />
}

function LearnSession() {
  const { items, completeLearning } = useVocab()
  const { press, reduce: shouldReduceMotion } = useSprings()

  const [deck, setDeck] = useState<readonly VocabItem[]>(() =>
    filterLearnDeck(items).slice(0, LEARN_SESSION_LIMIT),
  )
  const [total, setTotal] = useState(deck.length)
  const [doneCount, setDoneCount] = useState(0)
  const [exitCustom, setExitCustom] = useState<SwipeExit | null>(null)

  const current = deck[0]
  const phase = total > 0 && deck.length === 0 ? 'result' : 'answer'

  // 換卡即自動唸音：因果緊貼「新卡上前台」這個瞬間，不用等使用者點按鈕
  useEffect(() => {
    if (current) speakWord(current.word)
  }, [current])
  // 結算時剩餘的新字（超過本 session 上限的部分）；items 已被樂觀更新，直接重算
  const remaining = filterLearnDeck(items).length

  const handleSwipe = (dir: SwipeDirection, velocity: number) => {
    if (!current) return
    setExitCustom({ dir, velocity, reduce: shouldReduceMotion })
    if (dir === 'left') {
      // 再看一次：移到佇列尾端，純前端重排
      setDeck(d => [...d.slice(1), d[0]])
      return
    }
    // 記住了：樂觀更新，失敗撤回
    const captured = deck
    const capturedDone = doneCount
    setDeck(d => d.slice(1))
    setDoneCount(n => n + 1)
    void completeLearning(current.id).catch((err: unknown) => {
      setDeck(captured)
      setDoneCount(capturedDone)
      window.alert(
        `同步失敗（${err instanceof Error ? err.message : '未知錯誤'}），已退回本卡，請重試`,
      )
    })
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

  const restartSession = () => {
    const next = filterLearnDeck(items).slice(0, LEARN_SESSION_LIMIT)
    setDeck(next)
    setTotal(next.length)
    setDoneCount(0)
    setExitCustom(null)
  }

  if (total === 0) {
    return (
      <SessionShell>
        <SessionHeader status="" />
        <EmptyState
          icon={GraduationCap}
          title="沒有待學習的新字"
          description="在播放頁點擊字幕收錄新單字，就會出現在這裡"
          action={{ label: '去播放頁收錄', to: '/player' }}
        />
      </SessionShell>
    )
  }

  return (
    <SessionShell>
      <SessionHeader
        status={phase === 'result' ? '本輪學習完成' : `已學 ${doneCount} / ${total} 個`}
        progress={phase === 'answer' ? doneCount / total : undefined}
      />

      {phase === 'result' ? (
        <SessionSummary
          title="新字學習完成"
          stats={[{ label: '已加入複習', value: doneCount, tone: 'success' }]}
        >
          <p className="text-body tracking-body leading-body text-text-secondary">
            這些單字明天開始進入閃卡複習
          </p>
          {remaining > 0 && (
            <div>
              <Button variant="primary" onClick={restartSession}>
                再學 {Math.min(remaining, LEARN_SESSION_LIMIT)} 個
              </Button>
            </div>
          )}
        </SessionSummary>
      ) : current ? (
        <div className="flex-1 min-h-0 flex flex-col">
          <div className="relative flex-1 min-h-0">
            {deck.length > 1 && (
              <div
                aria-hidden
                className="absolute inset-0 scale-[0.96] translate-y-2 rounded-xl border border-border/30 material-regular shadow-md pointer-events-none"
              />
            )}
            <AnimatePresence custom={exitCustom} initial={false} mode="popLayout">
              <SwipeCard key={current.id} onSwipe={handleSwipe} leftLabel="再看一次" rightLabel="記住了">
                <div className="relative w-full h-full rounded-xl border border-border/30 material-regular shadow-md p-5 text-left space-y-3 overflow-y-auto">
                  <div className="flex items-center gap-2">
                    <p className="text-display tracking-display leading-display font-bold text-text-primary break-all">
                      {current.word}
                    </p>
                    <PronounceButton audioUrl={null} text={current.word} size={20} label="播放單字發音" />
                  </div>
                  {current.ipa && (
                    <p className="text-body tracking-body leading-body text-text-tertiary font-mono">{current.ipa}</p>
                  )}
                  <p className="text-title tracking-title leading-title font-semibold text-text-primary break-words whitespace-pre-line">
                    {formatMultiline(current.translation)}
                  </p>
                  {current.exampleEn && (
                    <div className="space-y-1">
                      <p className="text-body tracking-body leading-body text-text-secondary italic">{current.exampleEn}</p>
                      {current.exampleZh && (
                        <p className="text-caption tracking-caption leading-caption text-text-tertiary">{current.exampleZh}</p>
                      )}
                    </div>
                  )}
                  {current.sourceSentence && (
                    <div className="border-t border-border pt-3 space-y-2">
                      <p className="text-caption tracking-caption leading-caption text-text-tertiary italic">
                        {current.sourceSentence}
                      </p>
                      {current.sourceSentenceZh && (
                        <p className="text-caption tracking-caption leading-caption text-text-tertiary">
                          {current.sourceSentenceZh}
                        </p>
                      )}
                      {current.sourceEpisodeId && (
                        <ReplayAudioButton
                          episodeSlug={current.sourceEpisodeId}
                          timestamp={current.sourceTimestamp}
                          lineNo={current.sourceLineNo}
                        />
                      )}
                    </div>
                  )}
                  {current.mnemonic && (
                    <div className="pt-1">
                      <MnemonicHint text={current.mnemonic} />
                    </div>
                  )}
                </div>
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
              <span className="text-caption tracking-caption leading-caption font-medium">再看一次</span>
            </motion.button>
            <motion.button
              onClick={() => handleSwipe('right', 0)}
              whileTap={{ scale: 0.94 }}
              transition={press}
              className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-success/10 text-success shadow-sm hover:bg-success/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <Check size={18} />
              <span className="text-caption tracking-caption leading-caption font-medium">記住了</span>
            </motion.button>
          </div>
        </div>
      ) : null}
    </SessionShell>
  )
}
