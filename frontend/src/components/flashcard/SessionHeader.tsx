import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft } from 'lucide-react'
import { useSprings } from '../../lib/motion'

interface SessionHeaderProps {
  /** 右側狀態文字，如「第 3 / 10 張」 */
  readonly status: string
  /** 0–1；undefined 隱藏進度條（結算頁） */
  readonly progress?: number
}

/** 學習/複習/測驗三個 session 頁共用：返回鈕 + 進度文字 + 進度條。 */
export function SessionHeader({ status, progress }: SessionHeaderProps) {
  const navigate = useNavigate()
  const { snappy } = useSprings()
  return (
    <>
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={() => navigate('/vocab')}
          className="inline-flex items-center gap-1 min-h-[44px] -ml-2 px-2 rounded-md text-body tracking-body leading-body text-text-tertiary hover:text-text-secondary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <ArrowLeft size={16} />
          回到單字本
        </button>
        <p aria-live="polite" className="text-caption tracking-caption leading-caption text-text-tertiary tabular-nums">
          {status}
        </p>
      </div>
      {progress !== undefined && (
        <div className="h-1 rounded-full bg-border overflow-hidden mb-4">
          <motion.div
            className="h-full rounded-full bg-accent origin-left"
            initial={false}
            animate={{ scaleX: progress }}
            transition={snappy}
          />
        </div>
      )}
    </>
  )
}
