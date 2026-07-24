import { useState } from 'react'
import { motion } from 'framer-motion'
import { Eye, CheckCircle2, XCircle } from 'lucide-react'
import { buildCloze, checkClozeAnswer } from '../../lib/cloze'
import { formatMultiline } from '../../lib/format'
import { MnemonicHint } from '../wordcard/MnemonicHint'
import { PronounceButton } from '../wordcard/PronounceButton'
import { ReplayAudioButton } from './ReplayAudioButton'
import { Button } from '../primitives/Button'
import { useSprings } from '../../lib/motion'
import type { VocabItem } from '../../api/types'

interface ClozeCardProps {
  readonly item: VocabItem
  /** 挖空句渲染完由呼叫端算好傳入（缺 sourceSentence 時已 fallback 成 exampleEn）。 */
  readonly sentence: string
  readonly onGraded: (quality: number) => void
}

type Stage = 'answering' | 'graded'

/** 呼叫端務必掛 key={item.id}——換卡時要整個重新掛載才能重置輸入/揭曉狀態，
 * 不用 useEffect 同步（React Compiler 的 set-state-in-effect 規則不允許）。 */
export function ClozeCard({ item, sentence, onGraded }: ClozeCardProps) {
  const cloze = buildCloze(sentence, item.word)
  const [input, setInput] = useState('')
  const [stage, setStage] = useState<Stage>('answering')
  const [isCorrect, setIsCorrect] = useState(false)
  const { gentle } = useSprings()

  if (!cloze) return null // 呼叫端已 fallback 到辨識模式，理論上不會走到這裡

  const submit = () => {
    if (stage !== 'answering' || !input.trim()) return
    setIsCorrect(checkClozeAnswer(input, cloze.blank))
    setStage('graded')
  }

  const reveal = () => {
    setInput(cloze.blank)
    setIsCorrect(false)
    setStage('graded')
  }

  return (
    <div className="rounded-xl border border-border/30 material-regular shadow-md p-7 space-y-5">
      <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase">拼字填空</p>
      <p className="text-headline tracking-headline leading-headline font-medium text-text-primary">
        {cloze.before}
        <span className="inline-block min-w-[3em] border-b-2 border-accent mx-1 text-center font-semibold text-accent">
          {stage === 'graded' ? cloze.blank : '＿'.repeat(Math.max(cloze.blank.length, 3))}
        </span>
        {cloze.after}
      </p>
      <p className="text-body tracking-body leading-body text-text-secondary whitespace-pre-line">
        {formatMultiline(item.translation)}
      </p>

      {stage === 'answering' ? (
        <div className="space-y-3">
          <input
            autoFocus
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') submit() }}
            placeholder="輸入這個單字"
            className="w-full px-0 py-2.5 text-body tracking-body leading-body bg-transparent border-0 border-b-2 border-border text-text-primary placeholder:text-text-tertiary focus:outline-none focus:border-accent transition-colors duration-fast ease-apple"
          />
          <div className="flex items-center justify-between pt-1">
            <button
              onClick={reveal}
              className="inline-flex items-center gap-1.5 min-h-[44px] rounded-sm text-caption tracking-caption leading-caption text-text-tertiary hover:text-text-secondary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <Eye size={14} />
              看答案
            </button>
            <Button variant="primary" onClick={submit}>
              提交
            </Button>
          </div>
        </div>
      ) : (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={gentle}
          className="space-y-4"
        >
          <p
            className={`flex items-center gap-1.5 text-body tracking-body leading-body font-semibold ${isCorrect ? 'text-success' : 'text-warning'}`}
          >
            {isCorrect ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            {isCorrect ? '答對了！' : `正確答案：${cloze.blank}`}
            <PronounceButton audioUrl={null} text={cloze.blank} size={14} label="播放單字發音" />
          </p>
          {item.sourceEpisodeId && (
            <ReplayAudioButton episodeSlug={item.sourceEpisodeId} timestamp={item.sourceTimestamp} />
          )}
          {item.mnemonic && <MnemonicHint text={item.mnemonic} />}
          <Button variant="secondary" onClick={() => onGraded(isCorrect ? 5 : 1)} className="w-full justify-center">
            下一張
          </Button>
        </motion.div>
      )}
    </div>
  )
}
