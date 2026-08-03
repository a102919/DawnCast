import { useState } from 'react'
import { toast } from 'sonner'
import { CalendarCheck, BookOpen } from 'lucide-react'
import { useVocab } from '../state'
import { EmptyState } from '../components/primitives/EmptyState'
import { Button } from '../components/primitives/Button'
import { SessionHeader } from '../components/flashcard/SessionHeader'
import { SessionShell } from '../components/flashcard/SessionShell'
import { SessionSummary } from '../components/flashcard/SessionSummary'
import { buildSessionSteps, type SessionStep } from '../lib'
import { SessionStepRunner, type Outcome } from './SessionStepRunner'

interface StepResult {
  readonly step: SessionStep
  readonly outcome: Outcome
}

/** 智慧佇列 session：「開始學習」CTA 落地處。依單字成熟度 dispatch 四種卡面，
 *  session 上限 10，結算頁可「再來一輪」。 */
export function SessionRoute() {
  const { isLoading } = useVocab()
  if (isLoading) return null
  return <Session />
}

function Session() {
  const { items, completeLearning, updateCardReview, applyQuizRound } = useVocab()

  const [deck, setDeck] = useState<readonly SessionStep[]>(() => buildSessionSteps(items))
  const [idx, setIdx] = useState(0)
  const [results, setResults] = useState<readonly StepResult[]>([])

  const total = deck.length
  const phase = total > 0 && idx >= total ? 'result' : 'answer'
  const current = deck[idx]

  /** 算出當前 step 對應的 sync work：learn → completeLearning、複習 → updateCardReview review 模式、
   *  quiz → applyQuizRound。learn 的「再看一次」/cloze 看答案屬本地，不打 API。 */
  const workFor = (step: SessionStep, outcome: Outcome): (() => Promise<void>) => {
    if (step.kind === 'learn' && outcome === 'remembered') return () => completeLearning(step.item.id)
    if (step.kind === 'recognize' || step.kind === 'cloze') {
      const quality = outcome === 'remembered' ? 4 : 1
      return () => updateCardReview(step.item.id, quality, { mode: 'review' })
    }
    if (step.kind === 'quiz') return () => applyQuizRound(step.item.id, outcome === 'graduated')
    return () => Promise.resolve()
  }

  /** 樂觀寫入結果與前進，背景 work 失敗時只撤回這張卡自己的 entry——不能整批
   *  回退到呼叫當下的 snapshot，否則同時有多張卡在背景同步時，這張卡失敗會
   *  連帶把「呼叫之後才成功同步」的下一張卡也從本地狀態抹掉。 */
  const record = (step: SessionStep, outcome: Outcome) => {
    const entry: StepResult = { step, outcome }
    setResults(r => [...r, entry])
    setIdx(i => i + 1)
    void workFor(step, outcome)().catch((err: unknown) => {
      setResults(r => r.filter(e => e !== entry))
      setIdx(i => Math.max(0, i - 1))
      toast.error(
        `同步失敗（${err instanceof Error ? err.message : '未知錯誤'}），已退回本卡，請重試`,
      )
    })
  }

  const handleRestart = () => {
    setDeck(buildSessionSteps(items))
    setIdx(0)
    setResults([])
  }

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
    return (
      <SessionShell>
        <SessionHeader status="" />
        <EmptyState
          icon={CalendarCheck}
          title="今天沒有可學習的單字"
          description="表現很好！明天再來"
          action={{ label: '回單字本', to: '/vocab' }}
        />
      </SessionShell>
    )
  }

  const remembered = results.filter(r => r.outcome === 'remembered' || r.outcome === 'graduated').length
  const forgotten = results.filter(r => r.outcome === 'forgotten' || r.outcome === 'failed-quiz').length
  const graduated = results.filter(r => r.outcome === 'graduated').length

  return (
    <SessionShell>
      <SessionHeader
        status={phase === 'result' ? '本輪學習完成' : `第 ${idx + 1} / ${total} 張`}
        progress={phase === 'answer' ? idx / total : undefined}
      />

      {phase === 'result' ? (
        <SessionSummary
          title={forgotten === 0 ? '本輪全記得，太強了' : '本輪完成'}
          stats={[
            { label: '記得', value: remembered, tone: 'success' },
            { label: '不記得', value: forgotten, tone: 'warning' },
            ...(graduated > 0 ? [{ label: '精熟畢業', value: graduated, tone: 'success' as const }] : []),
          ]}
        >
          <div className="space-y-2">
            <p className="text-body tracking-body leading-body text-text-secondary">
              明天還會有新一輪準備好
            </p>
            <Button variant="primary" onClick={handleRestart}>
              再來一輪
            </Button>
          </div>
        </SessionSummary>
      ) : current ? (
        <SessionStepRunner
          step={current}
          onCommit={outcome => record(current, outcome)}
        />
      ) : null}
    </SessionShell>
  )
}
