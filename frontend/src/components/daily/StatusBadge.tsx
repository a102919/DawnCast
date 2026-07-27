import { CheckCircle2, Loader2, Lock, Plus, type LucideIcon } from 'lucide-react'
import type { DailyOrderStatus } from '../../api'

type Tone = 'neutral' | 'success' | 'warning' | 'accent'

interface Resolved {
  readonly icon: LucideIcon
  readonly badgeIcon: boolean
  readonly tone: Tone
  readonly label: string
}

function resolve(order: { status: DailyOrderStatus; ready?: boolean } | null, locked: boolean): Resolved {
  if (!order) return { icon: Plus, badgeIcon: false, tone: 'neutral', label: '未點' }
  if (order.status === 'played') return { icon: CheckCircle2, badgeIcon: true, tone: 'success', label: '已播放' }
  // queued 排在 locked 之前：T1 trigger 下單後立刻翻 queued，但今天晚下單
  // 時 deliveryTime 已過 cutoff → locked=true → 舊邏輯會顯示「已鎖定」蓋掉
  // 「生成中」，使用者以為「等隔天」。實際 worker 已 fire-and-forget 入列。
  // status 只有三態，queued→played 只在使用者實際按下播放才會翻；ready 補上
  // deliveries 是否已存在，避免內容早就生成完畢的舊訂單永遠卡在「生成中」。
  if (order.status === 'queued' && order.ready) {
    return { icon: CheckCircle2, badgeIcon: false, tone: 'accent', label: '可收聽' }
  }
  if (order.status === 'queued') return { icon: Loader2, badgeIcon: false, tone: 'accent', label: '生成中' }
  if (locked) return { icon: Lock, badgeIcon: true, tone: 'warning', label: '已鎖定' }
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
  readonly order: { status: DailyOrderStatus; ready?: boolean } | null
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
