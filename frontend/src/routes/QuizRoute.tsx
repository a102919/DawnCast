import { useMemo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Trophy } from 'lucide-react'
import { useVocab } from '../state'
import type { VocabItem } from '../api/types'
import { EmptyState } from '../components/primitives/EmptyState'
import { ClozeCard } from '../components/flashcard/ClozeCard'
import { SessionHeader } from '../components/flashcard/SessionHeader'
import { SessionShell } from '../components/flashcard/SessionShell'
import { SessionSummary } from '../components/flashcard/SessionSummary'
import { ChoiceQuestion } from '../components/quiz/ChoiceQuestion'
import { useSprings } from '../lib/motion'
import { filterQuizDeck, buildQuizRound } from '../lib'

type Outcome = 'mastered' | 'passed' | 'failed'

/** 畢業測驗：複習間隔 ≥ 21 天且到期的候選字，每字一輪 2 題（題型隨機不重複）。
 *  全對 streak+1（連 2 輪 → 精熟封存）；任一題錯即該輪失敗，回滑卡複習重新掙資格。 */
export function QuizRoute() {
  // 同 LearnRoute：deck 於內層 mount 凍結，等 Provider 載完再掛載
  const { isLoading } = useVocab()
  if (isLoading) return null
  return <QuizSession />
}

function QuizSession() {
  const { items, applyQuizRound } = useVocab()
  const { gentle } = useSprings()

  // deck 與干擾項池都在 mount 凍結，session 中的樂觀更新不會動搖題目
  const [deck] = useState<readonly VocabItem[]>(() => filterQuizDeck(items))
  const [pool] = useState<readonly VocabItem[]>(items)
  const [wordIdx, setWordIdx] = useState(0)
  const [qIdx, setQIdx] = useState(0)
  const [outcomes, setOutcomes] = useState<readonly { word: string; outcome: Outcome }[]>([])

  const current = deck[wordIdx]
  const phase = deck.length > 0 && wordIdx >= deck.length ? 'result' : 'answer'
  const questions = useMemo(
    () => (current ? buildQuizRound(current, pool) : []),
    [current, pool],
  )
  const question = questions[qIdx]

  const finishRound = (passed: boolean) => {
    if (!current) return
    const outcome: Outcome = passed
      ? (current.quizPassStreak ?? 0) >= 1 ? 'mastered' : 'passed'
      : 'failed'
    const capturedWordIdx = wordIdx
    const capturedOutcomes = outcomes
    setOutcomes(o => [...o, { word: current.word, outcome }])
    setQIdx(0)
    setWordIdx(i => i + 1)
    void applyQuizRound(current.id, passed).catch((err: unknown) => {
      setWordIdx(capturedWordIdx)
      setOutcomes(capturedOutcomes)
      window.alert(
        `同步失敗（${err instanceof Error ? err.message : '未知錯誤'}），已退回本字，請重試`,
      )
    })
  }

  const handleAnswer = (correct: boolean) => {
    if (!correct) {
      finishRound(false)
      return
    }
    if (qIdx + 1 < questions.length) setQIdx(q => q + 1)
    else finishRound(true)
  }

  if (deck.length === 0) {
    return (
      <SessionShell>
        <SessionHeader status="" />
        <EmptyState
          icon={Trophy}
          title="目前沒有畢業測驗候選"
          description="複習間隔累積到 21 天的單字，到期時會出現在這裡"
        />
      </SessionShell>
    )
  }

  const mastered = outcomes.filter(o => o.outcome === 'mastered')
  const passed = outcomes.filter(o => o.outcome === 'passed').length
  const failed = outcomes.filter(o => o.outcome === 'failed').length

  return (
    <SessionShell>
      <SessionHeader
        status={
          phase === 'result'
            ? '畢業測驗完成'
            : `第 ${wordIdx + 1} / ${deck.length} 個 · 第 ${qIdx + 1} 題`
        }
        progress={phase === 'answer' ? wordIdx / deck.length : undefined}
      />

      {phase === 'result' ? (
        <SessionSummary
          title={mastered.length > 0 ? '恭喜畢業！' : '本輪測驗完成'}
          stats={[
            { label: '精熟畢業', value: mastered.length, tone: 'success' },
            { label: '通過第一輪', value: passed, tone: 'default' },
            { label: '未通過', value: failed, tone: 'warning' },
          ]}
        >
          {mastered.length > 0 && (
            <p className="text-body tracking-body leading-body text-text-secondary break-words">
              已精熟封存：{mastered.map(o => o.word).join('、')}
            </p>
          )}
          {passed > 0 && (
            <p className="text-caption tracking-caption leading-caption text-text-tertiary">
              通過第一輪的單字一週後進行第二輪測驗
            </p>
          )}
          {failed > 0 && (
            <p className="text-caption tracking-caption leading-caption text-text-tertiary">
              未通過的單字已排回閃卡複習，累積熟練度後再挑戰
            </p>
          )}
        </SessionSummary>
      ) : current && question ? (
        <div className="flex-1 min-h-0 overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={`${current.id}-${qIdx}`}
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -24 }}
              transition={gentle}
            >
              {question.kind === 'cloze' ? (
                <ClozeCard
                  item={current}
                  sentence={question.sentence}
                  onGraded={q => handleAnswer(q === 5)}
                />
              ) : (
                <ChoiceQuestion question={question} onAnswered={handleAnswer} />
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      ) : null}
    </SessionShell>
  )
}
