import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Sparkles } from 'lucide-react'
import { Button } from '../primitives/Button'
import { StatCard } from '../primitives/StatCard'
import { useSprings } from '../../lib/motion'

export interface SummaryStat {
  readonly label: string
  readonly value: number
  readonly tone: 'default' | 'success' | 'warning' | 'danger'
}

interface SessionSummaryProps {
  readonly title: string
  readonly stats: readonly SummaryStat[]
  /** 額外文案或動作（如「再學 N 個」續學鈕），插在統計與返回鈕之間 */
  readonly children?: ReactNode
}

/** 學習/複習/測驗三個 session 頁共用的結算卡。 */
export function SessionSummary({ title, stats, children }: SessionSummaryProps) {
  const navigate = useNavigate()
  const { gentle } = useSprings()
  const cols = stats.length === 2 ? 'grid-cols-2' : 'grid-cols-3'
  return (
    <motion.div
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      transition={gentle}
      className="rounded-xl border border-border/30 material-regular shadow-lg p-8 text-center space-y-5"
    >
      <motion.div
        initial={{ scale: 0.85, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ ...gentle, delay: 0.1 }}
        className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-accent/10 text-accent"
      >
        <Sparkles size={24} />
      </motion.div>
      <h2 className="text-title tracking-title leading-title font-semibold text-text-primary">{title}</h2>
      <div className={`grid ${cols} gap-3 max-w-sm mx-auto`}>
        {stats.map((s, i) => (
          <motion.div
            key={s.label}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...gentle, delay: i * 0.05 }}
          >
            <StatCard label={s.label} value={s.value} tone={s.tone} />
          </motion.div>
        ))}
      </div>
      {children}
      <div>
        <Button variant="secondary" onClick={() => navigate('/vocab')}>
          回到單字本
        </Button>
      </div>
    </motion.div>
  )
}
