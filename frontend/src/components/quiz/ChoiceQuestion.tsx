import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { CheckCircle2, XCircle, Volume2 } from 'lucide-react'
import { Button } from '../primitives/Button'
import { useSprings } from '../../lib/motion'
import { formatMultiline } from '../../lib/format'
import { speakWord } from '../../lib/speech'
import type { QuizQuestion } from '../../lib/quiz'

const KIND_LABELS = {
  en2zh: '選出正確翻譯',
  zh2en: '選出正確單字',
  listening: '聽發音選出意思',
} as const

function optionStateClass(answered: boolean, isAnswer: boolean, isSelected: boolean): string {
  if (!answered) return 'border-border hover:border-accent/40 hover:bg-accent/5'
  if (isAnswer) return 'border-success bg-success/10 text-success'
  if (isSelected) return 'border-warning bg-warning/10 text-warning'
  return 'border-border opacity-50'
}

interface ChoiceQuestionProps {
  readonly question: Extract<QuizQuestion, { kind: 'en2zh' | 'zh2en' | 'listening' }>
  readonly onAnswered: (correct: boolean) => void
}

/** 三種選擇題共用（英→中／中→英／聽力）。選後立即顯示對錯，按「下一題」續行。
 *  呼叫端務必掛 key——換題時整個重新掛載重置選取狀態。 */
export function ChoiceQuestion({ question, onAnswered }: ChoiceQuestionProps) {
  const { gentle } = useSprings()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const answered = selectedId !== null
  const isListening = question.kind === 'listening'

  // 聽力題進場自動播一次發音；離開這題（換題重新掛載／整個 quiz 中途離開）時
  // 要取消還在講的 utterance，否則使用者已經跳到下一步，瀏覽器還在背景唸上一題。
  useEffect(() => {
    if (isListening) speakWord(question.item.word)
    return () => {
      if ('speechSynthesis' in window) window.speechSynthesis.cancel()
    }
  }, [isListening, question.item.word])

  return (
    <div className="rounded-xl border border-border/30 material-regular shadow-md p-7 space-y-5">
      <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase">
        {KIND_LABELS[question.kind]}
      </p>

      {isListening ? (
        <button
          type="button"
          onClick={() => speakWord(question.item.word)}
          className="inline-flex items-center gap-2 min-h-[44px] px-4 rounded-lg bg-accent/10 text-accent hover:bg-accent/15 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <Volume2 size={18} />
          <span className="text-body tracking-body leading-body font-medium">重播發音</span>
        </button>
      ) : (
        <p className="text-headline tracking-headline leading-headline font-semibold text-text-primary break-words whitespace-pre-line">
          {question.kind === 'en2zh' ? question.prompt : formatMultiline(question.prompt)}
        </p>
      )}

      <div className="space-y-2" role="radiogroup" aria-label="選項">
        {question.options.map(option => {
          const isAnswer = option.id === question.answerId
          const isSelected = option.id === selectedId
          const stateClass = optionStateClass(answered, isAnswer, isSelected)
          return (
            <button
              key={option.id}
              type="button"
              disabled={answered}
              onClick={() => setSelectedId(option.id)}
              className={`w-full flex items-center justify-between gap-2 min-h-[52px] px-4 py-2.5 rounded-lg border text-left text-body tracking-body leading-body text-text-primary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${stateClass}`}
            >
              <span className="break-words whitespace-pre-line">{formatMultiline(option.label)}</span>
              {answered && isAnswer && <CheckCircle2 size={18} className="shrink-0 text-success" />}
              {answered && isSelected && !isAnswer && <XCircle size={18} className="shrink-0 text-warning" />}
            </button>
          )
        })}
      </div>

      {answered && (
        <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={gentle}>
          <Button
            variant="secondary"
            onClick={() => onAnswered(selectedId === question.answerId)}
            className="w-full justify-center"
          >
            下一題
          </Button>
        </motion.div>
      )}
    </div>
  )
}
