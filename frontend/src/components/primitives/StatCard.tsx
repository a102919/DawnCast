import type { LucideIcon } from 'lucide-react'

type Tone = 'default' | 'success' | 'warning'

interface StatCardProps {
  readonly icon?: LucideIcon
  readonly label: string
  readonly value: string | number
  readonly unit?: string
  readonly tone?: Tone
}

const toneClass: Record<Tone, { container: string; value: string }> = {
  default: { container: 'material-thin border-border/60 shadow-sm', value: 'text-text-primary' },
  success: { container: 'bg-success/10 border-success/30', value: 'text-success' },
  warning: { container: 'bg-warning/10 border-warning/30', value: 'text-warning' },
}

export function StatCard({ icon: Icon, label, value, unit, tone = 'default' }: StatCardProps) {
  const { container, value: valueClass } = toneClass[tone]
  return (
    <div className={`p-4 rounded-xl border text-center space-y-1 ${container}`}>
      {Icon && (
        <div
          className={`inline-flex items-center justify-center w-8 h-8 rounded-full mb-1 ${
            tone === 'default' ? 'bg-accent/10 text-accent' : `bg-current/10 ${valueClass}`
          }`}
        >
          <Icon size={16} />
        </div>
      )}
      <div className={`text-2xl font-bold ${valueClass}`}>
        {value}
        {unit && <span className="text-sm font-normal text-text-secondary ml-0.5">{unit}</span>}
      </div>
      <div className="text-xs text-text-tertiary">{label}</div>
    </div>
  )
}
