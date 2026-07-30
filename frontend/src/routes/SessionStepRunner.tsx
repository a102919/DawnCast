import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Check, X, RotateCw, BookOpen, GraduationCap } from 'lucide-react'
import type { Transition } from 'framer-motion'
import type { VocabItem } from '../api/types'
import { SwipeCard } from '../components/flashcard/SwipeCard'
import { ClozeCard } from '../components/flashcard/ClozeCard'
import { MnemonicHint } from '../components/wordcard/MnemonicHint'
import { PronounceButton } from '../components/wordcard/PronounceButton'
import { ReplayAudioButton } from '../components/flashcard/ReplayAudioButton'
import { ChoiceQuestion } from '../components/quiz/ChoiceQuestion'
import { useSprings } from '../lib/motion'
import { buildQuizRound, formatMultiline } from '../lib'
import type { SessionStep } from '../lib'
import { speakWord } from '../lib/speech'
import type { SwipeDirection, SwipeExit } from '../lib/swipe'
import { useVocab } from '../state'

export type Outcome = 'remembered' | 'forgotten' | 'graduated' | 'failed-quiz'

interface SessionStepRunnerProps {
  readonly step: SessionStep
  readonly onCommit: (outcome: Outcome) => void
}

/** 智慧佇列單一步驟渲染器：把 SessionStep 對應到既有四種卡面。
 *  learn 卡 → 二元 SwipeCard（記住了 / 再看一次）
 *  recognize 卡 → 翻卡辨識 SwipeCard（記得 / 不記得）
 *  cloze 卡 → 既有 ClozeCard（q=5 對、q=1 錯或看答案）
 *  quiz 卡 → buildQuizRound 展開 2 題，答錯即結束該 step */
export function SessionStepRunner({ step, onCommit }: SessionStepRunnerProps) {
  switch (step.kind) {
    case 'learn':
      return <LearnCard item={step.item} onCommit={onCommit} />
    case 'recognize':
      return <RecognizeCard item={step.item} onCommit={onCommit} />
    case 'cloze':
      return (
        <ClozeCard
          key={step.item.id}
          item={step.item}
          sentence={step.item.sourceSentence || step.item.exampleEn || ''}
          onGraded={q => onCommit(q >= 3 ? 'remembered' : 'forgotten')}
        />
      )
    case 'quiz':
      return <QuizRound item={step.item} onCommit={onCommit} />
  }
}

// ---------- learn：二元滑卡，右滑記住、左滑再看一次 ----------

function LearnCard({
  item,
  onCommit,
}: {
  readonly item: VocabItem
  readonly onCommit: (outcome: Outcome) => void
}) {
  const { press } = useSprings()
  const [exitCustom, setExitCustom] = useState<SwipeExit | null>(null)

  const swipe = (dir: SwipeDirection, velocity: number) => {
    setExitCustom({ dir, velocity, reduce: false })
    if (dir === 'right') onCommit('remembered')
    else onCommit('forgotten')
  }

  // 因果緊貼「新卡上前台」這個瞬間
  useEffect(() => { speakWord(item.word) }, [item.word])

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="relative flex-1 min-h-0">
        <AnimatePresence custom={exitCustom} initial={false} mode="popLayout">
          <SwipeCard key={item.id} onSwipe={swipe} leftLabel="再看一次" rightLabel="記住了">
            <div className="relative h-full rounded-xl border border-border/30 material-regular shadow-md p-5 text-left space-y-3 overflow-y-auto">
              <div className="flex items-center gap-2">
                <p className="text-display tracking-display leading-display font-bold text-text-primary break-all">{item.word}</p>
                <PronounceButton audioUrl={null} text={item.word} size={20} label="播放單字發音" />
              </div>
              {item.ipa && (
                <p className="text-body tracking-body leading-body text-text-tertiary font-mono">{item.ipa}</p>
              )}
              <p className="text-title tracking-title leading-title font-semibold text-text-primary break-words whitespace-pre-line">
                {formatMultiline(item.translation)}
              </p>
              {item.exampleEn && (
                <div className="space-y-1">
                  <p className="text-body tracking-body leading-body text-text-secondary italic">{item.exampleEn}</p>
                  {item.exampleZh && (
                    <p className="text-caption tracking-caption leading-caption text-text-tertiary">{item.exampleZh}</p>
                  )}
                </div>
              )}
              {item.sourceSentence && (
                <div className="border-t border-border pt-3 space-y-2">
                  <p className="text-caption tracking-caption leading-caption text-text-tertiary italic">{item.sourceSentence}</p>
                  {item.sourceSentenceZh && (
                    <p className="text-caption tracking-caption leading-caption text-text-tertiary">{item.sourceSentenceZh}</p>
                  )}
                  <ReplayAudioButton episodeSlug={item.sourceEpisodeId} timestamp={item.sourceTimestamp} lineNo={item.sourceLineNo} />
                </div>
              )}
              {item.mnemonic && (
                <div className="pt-1"><MnemonicHint text={item.mnemonic} /></div>
              )}
              <p className="inline-flex items-center gap-1 text-caption tracking-caption leading-caption text-text-tertiary">
                <BookOpen size={12} />
                左滑再看一次・右滑記住了
              </p>
            </div>
          </SwipeCard>
        </AnimatePresence>
      </div>
      <FallbackSwipeButtons leftLabel="再看一次" rightLabel="記住了" onSwipe={swipe} press={press} />
    </div>
  )
}

// ---------- recognize：翻卡辨識 ----------

function RecognizeCard({
  item,
  onCommit,
}: {
  readonly item: VocabItem
  readonly onCommit: (outcome: Outcome) => void
}) {
  const { press, gentle } = useSprings()
  const [flipped, setFlipped] = useState(false)
  const [exitCustom, setExitCustom] = useState<SwipeExit | null>(null)

  const swipe = (dir: SwipeDirection, velocity: number) => {
    setExitCustom({ dir, velocity, reduce: false })
    if (dir === 'right') onCommit('remembered')
    else onCommit('forgotten')
  }
  // ref trick：鍵盤 handler 用 ref 取得目前 swipe，effect empty deps 是刻意的
  // ——同 FlashcardRoute 既有 pattern
  const swipeRef = useRef(swipe)
  useEffect(() => { swipeRef.current = swipe })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') swipeRef.current('right', 0)
      if (e.key === 'ArrowLeft') swipeRef.current('left', 0)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="relative flex-1 min-h-0">
        <AnimatePresence custom={exitCustom} initial={false} mode="popLayout">
          <SwipeCard key={item.id} onSwipe={swipe} leftLabel="不記得" rightLabel="記得">
            <motion.div
              role="button"
              tabIndex={0}
              onClick={() => setFlipped(f => !f)}
              onKeyDown={e => {
                if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); setFlipped(f => !f) }
              }}
              aria-label={flipped ? '顯示單字面' : '顯示翻譯面'}
              className="relative block w-full h-full text-left [perspective:1600px] rounded-xl cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <motion.div
                className="relative w-full h-full [transform-style:preserve-3d]"
                animate={{ rotateY: flipped ? 180 : 0 }}
                transition={gentle}
              >
                <div className="absolute inset-0 [backface-visibility:hidden] rounded-xl border border-border/30 material-regular shadow-md p-7 text-center flex flex-col items-center justify-center">
                  <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase mb-3">單字</p>
                  <div className="flex items-center justify-center gap-2">
                    <p className="text-display tracking-display leading-display font-bold text-text-primary break-all">{item.word}</p>
                    <PronounceButton audioUrl={null} text={item.word} size={20} label="播放單字發音" />
                  </div>
                  {item.ipa && (
                    <p className="text-body tracking-body leading-body text-text-tertiary font-mono mt-2">{item.ipa}</p>
                  )}
                  <p className="inline-flex items-center gap-1 mt-6 text-caption tracking-caption leading-caption text-text-tertiary">
                    <RotateCw size={12} />
                    點擊翻面・左右滑動評分
                  </p>
                </div>
                <div className="absolute inset-0 [backface-visibility:hidden] [transform:rotateY(180deg)] rounded-xl border border-border/30 material-regular shadow-md p-7 text-left space-y-4 overflow-y-auto">
                  <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase">翻譯</p>
                  <p className="text-title tracking-title leading-title font-semibold text-text-primary break-words whitespace-pre-line">
                    {formatMultiline(item.translation)}
                  </p>
                  {item.mnemonic && <MnemonicHint text={item.mnemonic} />}
                </div>
              </motion.div>
            </motion.div>
          </SwipeCard>
        </AnimatePresence>
      </div>
      <FallbackSwipeButtons leftLabel="不記得" rightLabel="記得" onSwipe={swipe} press={press} />
    </div>
  )
}

// ---------- quiz：2 題 round，答錯即結束該 step ----------

function QuizRound({
  item,
  onCommit,
}: {
  readonly item: VocabItem
  readonly onCommit: (outcome: Outcome) => void
}) {
  const { items } = useVocab()
  const [questions] = useState(() => buildQuizRound(item, items))
  const [qIdx, setQIdx] = useState(0)
  const question = questions[qIdx]

  const answer = (correct: boolean) => {
    if (!correct) {
      onCommit('failed-quiz')
      return
    }
    if (qIdx + 1 < questions.length) {
      setQIdx(q => q + 1)
    } else {
      onCommit('graduated')
    }
  }

  if (!question) return null
  return (
    <div className="flex-1 min-h-0 overflow-y-auto">
      <AnimatePresence mode="wait">
        <motion.div
          key={`${item.id}-${qIdx}`}
          initial={{ opacity: 0, x: 24 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -24 }}
        >
          {question.kind === 'cloze' ? (
            <ClozeCard
              key={`${item.id}-${qIdx}-cloze`}
              item={item}
              sentence={question.sentence}
              onGraded={q => answer(q === 5)}
            />
          ) : (
            <ChoiceQuestion question={question} onAnswered={answer} />
          )}
        </motion.div>
      </AnimatePresence>
      <div className="mt-4 flex items-center justify-center gap-4 text-caption tracking-caption leading-caption text-text-tertiary">
        <span>第 {qIdx + 1} / {questions.length} 題</span>
        <GraduationCap size={14} />
      </div>
    </div>
  )
}

// ---------- 共用：底部無障礙按鈕 ----------

function FallbackSwipeButtons({
  leftLabel,
  rightLabel,
  onSwipe,
  press,
}: {
  readonly leftLabel: string
  readonly rightLabel: string
  readonly onSwipe: (dir: SwipeDirection, velocity: number) => void
  readonly press: Transition
}) {
  return (
    <div className="mt-4 grid grid-cols-2 gap-3">
      <motion.button
        onClick={() => onSwipe('left', 0)}
        whileTap={{ scale: 0.94 }}
        transition={press}
        className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-warning/10 text-warning shadow-sm hover:bg-warning/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <X size={18} />
        <span className="text-caption tracking-caption leading-caption font-medium">{leftLabel}</span>
      </motion.button>
      <motion.button
        onClick={() => onSwipe('right', 0)}
        whileTap={{ scale: 0.94 }}
        transition={press}
        className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-success/10 text-success shadow-sm hover:bg-success/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        <Check size={18} />
        <span className="text-caption tracking-caption leading-caption font-medium">{rightLabel}</span>
      </motion.button>
    </div>
  )
}
