import { ArrowRight, Play } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import type { VocabItem } from '../../api/types'
import { buildSessionSteps } from '../../lib/srs'
import { toIsoDate } from '../../lib/dailyOrderDate'

interface StartSessionButtonProps {
  readonly items: readonly VocabItem[]
}

/** 單字本主 CTA「開始學習 N 張」：點擊進 /session。
 *  accent 色塊（淺 backdrop + 亮頂邊），沿襲 apple-design 浮動材質層。
 *  N=0 時 disabled 並顯示副文案，避免空點造成閃頁。 */
export function StartSessionButton({ items }: StartSessionButtonProps) {
  const navigate = useNavigate()
  const count = buildSessionSteps(items, toIsoDate(new Date())).length
  const disabled = count === 0

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => navigate('/session')}
      className={`group w-full flex items-center justify-between gap-3 p-4 rounded-xl border transition-all duration-fast ease-apple
        ${disabled
          ? 'bg-bg-secondary/40 border-border/30 cursor-not-allowed opacity-60'
          : 'bg-accent/12 border-accent/40 hover:bg-accent/20 active:scale-[0.98] shadow-sm hover:shadow-md cursor-pointer'}
        focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}
    >
      <div className="flex items-center gap-3 min-w-0">
        <span className={`inline-flex items-center justify-center w-10 h-10 rounded-full shrink-0 ${disabled ? 'bg-bg-tertiary text-text-tertiary' : 'bg-accent text-white shadow-sm'}`}>
          <Play size={18} fill="currentColor" />
        </span>
        <div className="min-w-0 text-left">
          <p className="text-title tracking-title leading-title font-semibold text-text-primary">
            開始學習
          </p>
          <p className="text-caption tracking-caption leading-caption text-text-secondary mt-0.5 truncate">
            {disabled
              ? '先到播放頁收幾個字再開始'
              : count >= 1
                ? `今天還有 ${count} 張可以練習`
                : '今天沒有可練習的單字'}
          </p>
        </div>
      </div>
      {!disabled && (
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-display tracking-display leading-display font-bold text-accent tabular-nums">
            {count}
          </span>
          <ArrowRight size={18} className="text-accent group-hover:translate-x-1 transition-transform duration-fast ease-apple" />
        </div>
      )}
    </button>
  )
}
