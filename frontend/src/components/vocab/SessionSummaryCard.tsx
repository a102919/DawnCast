import { CalendarCheck, GraduationCap, Trophy } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { VocabItem } from '../../api/types'
import { buildSessionSteps, filterLearnDeck } from '../../lib/srs'
import { toIsoDate } from '../../lib/dailyOrderDate'

interface StatRow {
  readonly icon: LucideIcon
  readonly label: string
  readonly value: number
}

interface SessionSummaryCardProps {
  readonly items: readonly VocabItem[]
}

/** 單字本首頁的學習狀態摘要：今天可學 / 待學習 / 已精熟。
 *  「今天可學」= 智慧佇列總長（用 buildSessionSteps 共用同一語意，避免 badge 與 CTA 對不上）。 */
export function SessionSummaryCard({ items }: SessionSummaryCardProps) {
  const today = toIsoDate(new Date())
  const readyToLearn = buildSessionSteps(items, today).length
  const learn = filterLearnDeck(items).length
  const mastered = items.filter(v => v.status === 5).length

  const rows: readonly StatRow[] = [
    { icon: CalendarCheck, label: '今天可學', value: readyToLearn },
    { icon: GraduationCap, label: '待學習', value: learn },
    { icon: Trophy, label: '已精熟', value: mastered },
  ]

  return (
    <div className="rounded-xl border border-border/30 bg-bg-secondary/60 material-regular p-4">
      <p className="text-caption tracking-caption leading-caption font-semibold text-text-tertiary uppercase mb-2.5">
        本日學習狀態
      </p>
      <div className="grid grid-cols-3 gap-2">
        {rows.map(row => (
          <div key={row.label} className="flex flex-col items-center gap-1">
            <row.icon size={18} className="text-accent" aria-hidden />
            <span className="text-title tracking-title leading-title font-semibold text-text-primary tabular-nums">
              {row.value}
            </span>
            <span className="text-caption tracking-caption leading-caption text-text-tertiary">
              {row.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
