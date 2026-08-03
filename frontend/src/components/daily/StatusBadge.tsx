import { Ban, CheckCircle2, Loader2, Plus, type LucideIcon } from 'lucide-react'
import type { DailyOrderStatus } from '../../api'

type Tone = 'neutral' | 'success' | 'accent' | 'muted'

interface Resolved {
  readonly icon: LucideIcon
  readonly badgeIcon: boolean
  readonly tone: Tone
  readonly label: string
  readonly spin: boolean
}

/** 隨時點餐下狀態完全由 order.status 推導，不再需要呼叫端另外算 locked 傳進來——
 *  status !== 'played' 本身就是「還在生成中或已在收聽，不能再變動」的完整定義。
 *  status='ready'（migration 0025）本身就是「生成完成、可收聽」，不用再另外
 *  查 deliveries 是否存在。
 *
 *  status='expired'（migration 0027）：reconcile 退役的卡死訂單，跟 played
 *  不同調——played 是「使用者實際聽完」、expired 是「系統放棄」，
 *  顯示為「已放棄」+ Ban icon + muted tone，避免誤導使用者以為聽完了。 */
function resolve(order: { status: DailyOrderStatus } | null): Resolved {
  if (!order) return { icon: Plus, badgeIcon: false, tone: 'neutral', label: '未點', spin: false }
  if (order.status === 'played') {
    return { icon: CheckCircle2, badgeIcon: true, tone: 'success', label: '已播放', spin: false }
  }
  if (order.status === 'ready') {
    return { icon: CheckCircle2, badgeIcon: false, tone: 'accent', label: '可收聽', spin: false }
  }
  if (order.status === 'queued' || order.status === 'pending') {
    return { icon: Loader2, badgeIcon: false, tone: 'accent', label: '生成中', spin: true }
  }
  if (order.status === 'expired') {
    return { icon: Ban, badgeIcon: false, tone: 'muted', label: '已放棄', spin: false }
  }
  return { icon: CheckCircle2, badgeIcon: false, tone: 'accent', label: '已送出', spin: false }
}

const badgeToneClass: Record<Tone, string> = {
  neutral: 'bg-bg-secondary text-text-tertiary border-border',
  success: 'bg-success/10 text-success border-success/20',
  accent: 'bg-accent/10 text-accent border-accent/20',
  muted: 'bg-bg-secondary text-text-tertiary border-border',
}

const textToneClass: Record<Tone, string> = {
  neutral: 'text-text-tertiary',
  success: 'text-success',
  accent: 'text-accent',
  muted: 'text-text-tertiary',
}

interface StatusBadgeProps {
  readonly order: { status: DailyOrderStatus } | null
  readonly display: 'badge' | 'text' | 'icon'
  readonly size?: number
}

export function StatusBadge({ order, display, size = 14 }: StatusBadgeProps) {
  const { icon: Icon, badgeIcon, tone, label, spin } = resolve(order)
  const spinClass = spin ? 'motion-safe:animate-spin' : ''

  if (display === 'icon') {
    return <Icon size={size} className={`${textToneClass[tone]} ${spinClass}`} aria-hidden />
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
