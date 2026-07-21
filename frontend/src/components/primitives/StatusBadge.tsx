import { CheckCircle2, Lock, Plus, type LucideIcon } from 'lucide-react'
import type { DailyOrderStatus } from '../../api'

type Tone = 'neutral' | 'success' | 'warning' | 'accent'

interface Resolved {
  readonly icon: LucideIcon
  readonly badgeIcon: boolean
  readonly tone: Tone
  readonly label: string
}

function resolve(order: { status: DailyOrderStatus } | null, locked: boolean): Resolved {
  if (!order) return { icon: Plus, badgeIcon: false, tone: 'neutral', label: '未點' }
  if (order.status === 'played') return { icon: CheckCircle2, badgeIcon: true, tone: 'success', label: '已播放' }
  if (locked) return { icon: Lock, badgeIcon: true, tone: 'warning', label: '已鎖定' }
  if (order.status === 'queued') return { icon: CheckCircle2, badgeIcon: false, tone: 'accent', label: '已排入' }
  return { icon: CheckCircle2, badgeIcon: false, tone: 'accent', label: '已送出' }
}

const badgeToneClass: Record<Tone, string> = {
  neutral: 'bg-bg-secondary text-text-tertiary border-border',
  success: 'bg-success/10 text-success border-success/20',
  warning: 'bg-warning/10 text-warning border-warning/20',
  accent: 'bg-accent/10 text-accent border-accent/20',
}

const textToneClass: Record<Tone, string> = {
  neutral: 'text-text-tertiary',
  success: 'text-success',
  warning: 'text-warning',
  accent: 'text-accent',
}

interface StatusBadgeProps {
  readonly order: { status: DailyOrderStatus } | null
  readonly locked: boolean
  readonly display: 'badge' | 'text' | 'icon'
  readonly size?: number
}

export function StatusBadge({ order, locked, display, size = 14 }: StatusBadgeProps) {
  const { icon: Icon, badgeIcon, tone, label } = resolve(order, locked)

  if (display === 'icon') {
    return <Icon size={size} className={textToneClass[tone]} aria-hidden />
  }

  if (display === 'text') {
    return <span className={textToneClass[tone]}>{label}</span>
  }

  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border ${badgeToneClass[tone]}`}>
      {badgeIcon && <Icon size={10} aria-hidden />}
      {label}
    </span>
  )
}
