import { Lock, CheckCircle2, Plus } from 'lucide-react'
import { isOrderLocked, getWeekdayLabel } from '../../lib/dailyOrderDate'
import type { DailyOrder } from '../../api'
import { StatusBadge } from './StatusBadge'

function getDayNumber(iso: string): string {
  const parts = iso.split('-')
  return parts[2] ?? ''
}

interface DailyCalendarProps {
  readonly today: string
  readonly dates: readonly string[]
  readonly selectedDate: string
  readonly getOrder: (date: string) => DailyOrder | null
  readonly onSelect: (date: string) => void
}

export function DailyCalendar({ today, dates, selectedDate, getOrder, onSelect }: DailyCalendarProps) {
  return (
    <section>
      <h2 className="sr-only">未來 7 天行事曆</h2>
      <div className="grid grid-cols-7 gap-1.5">
        {dates.map(date => {
          const order = getOrder(date)
          const isToday = date === today
          const isSelected = date === selectedDate
          const locked = order ? isOrderLocked(order) : false

          const baseClass =
            'flex flex-col items-center justify-center gap-0.5 py-2.5 rounded-lg border transition-colors duration-fast ease-apple min-h-[64px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1'
          const stateClass = isSelected
            ? ' border-accent bg-accent/10 text-accent'
            : isToday
              ? ' border-accent/40 bg-bg-primary text-text-primary hover:bg-bg-secondary'
              : ' border-border bg-bg-primary text-text-primary hover:bg-bg-secondary'

          return (
            <button
              key={date}
              type="button"
              onClick={() => onSelect(date)}
              className={`${baseClass} ${stateClass}`}
              aria-pressed={isSelected}
              aria-label={`${date}${order ? '，已送出訂單' : '，未點餐'}${locked ? '，已鎖定' : ''}`}
            >
              <span className="text-[10px] text-text-tertiary">{getWeekdayLabel(date)}</span>
              <span className="text-base font-semibold leading-none">{getDayNumber(date)}</span>
              <span className="text-[10px] mt-0.5 h-3 flex items-center justify-center">
                <StatusBadge order={order} locked={locked} display="icon" size={12} />
              </span>
            </button>
          )
        })}
      </div>
      <p className="text-[11px] text-text-tertiary mt-2 leading-relaxed">
        <PlusIcon /> 可加點 · <CheckIcon /> 已送出 · <LockIcon /> 已鎖定
      </p>
    </section>
  )
}

function PlusIcon() {
  return <Plus size={10} className="inline -mt-0.5 text-text-tertiary" aria-hidden />
}
function CheckIcon() {
  return <CheckCircle2 size={10} className="inline -mt-0.5 text-accent" aria-hidden />
}
function LockIcon() {
  return <Lock size={10} className="inline -mt-0.5 text-warning" aria-hidden />
}