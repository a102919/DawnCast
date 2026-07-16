import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown, CheckCircle2, Lock, Play } from 'lucide-react'
import { Button } from '../primitives'
import { TOPIC_LABELS, formatDateZhTW } from '../../routes/episodeData'
import type { DailyOrder, DailyOrderStatus } from '../../api'
import { isOrderLocked, isToday, getWeekdayLabel } from '../../lib/dailyOrderDate'

const COLLAPSED_LIMIT = 3

interface DailyOrderHistoryProps {
  readonly today: string
  readonly orders: ReadonlyMap<string, DailyOrder>
  readonly selectedDate: string
  readonly onSelectDate: (date: string) => void
}

export function DailyOrderHistory({ today, orders, selectedDate, onSelectDate }: DailyOrderHistoryProps) {
  const all = [...orders.values()].sort((a, b) => a.date.localeCompare(b.date))

  const todayOrders = all.filter(o => isToday(o.date, new Date(today + 'T00:00:00')))
  const futureOrders = all.filter(o => o.date > today)
  const pastOrders = all.filter(o => o.date < today)

  const [pastExpanded, setPastExpanded] = useState(false)

  const visiblePast = pastExpanded ? pastOrders : pastOrders.slice(0, COLLAPSED_LIMIT)

  const hasAny = todayOrders.length + futureOrders.length + pastOrders.length > 0

  return (
    <section className="space-y-4">
      <h2 className="text-sm font-semibold text-text-tertiary uppercase tracking-wider">訂單紀錄</h2>

      {!hasAny && (
        <div className="rounded-lg border border-dashed border-border bg-bg-secondary/30 px-4 py-6 text-center text-xs text-text-tertiary">
          目前還沒有任何訂單,點上方任一天送出第一餐吧。
        </div>
      )}

      {todayOrders.length > 0 && (
        <Group title="今天">
          {todayOrders.map(o => (
            <OrderRow
              key={o.date}
              order={o}
              selected={o.date === selectedDate}
              onClick={() => onSelectDate(o.date)}
            />
          ))}
        </Group>
      )}

      {futureOrders.length > 0 && (
        <Group title="未來">
          {futureOrders.map(o => (
            <OrderRow
              key={o.date}
              order={o}
              selected={o.date === selectedDate}
              onClick={() => onSelectDate(o.date)}
            />
          ))}
        </Group>
      )}

      {pastOrders.length > 0 && (
        <Group title="過去">
          <AnimatePresence initial={false}>
            {visiblePast.map(o => (
              <motion.div
                key={o.date}
                layout
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
                className="overflow-hidden"
              >
                <OrderRow
                  order={o}
                  selected={o.date === selectedDate}
                  onClick={() => onSelectDate(o.date)}
                />
              </motion.div>
            ))}
          </AnimatePresence>
          {pastOrders.length > COLLAPSED_LIMIT && (
            <div className="pt-1 flex justify-center">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPastExpanded(v => !v)}
              >
                <ChevronDown
                  size={12}
                  className={`transition-transform duration-fast ${pastExpanded ? 'rotate-180' : ''}`}
                />
                {pastExpanded ? '收合過去紀錄' : `展開更多（還有 ${pastOrders.length - COLLAPSED_LIMIT} 筆）`}
              </Button>
            </div>
          )}
        </Group>
      )}
    </section>
  )
}

function Group({ title, children }: { readonly title: string; readonly children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-text-tertiary">{title}</h3>
      <div className="divide-y divide-border rounded-lg border border-border bg-bg-primary overflow-hidden">
        {children}
      </div>
    </div>
  )
}

function OrderRow({
  order,
  selected,
  onClick,
}: {
  readonly order: DailyOrder
  readonly selected: boolean
  readonly onClick: () => void
}) {
  const locked = isOrderLocked(order)
  const topicSummary = order.selectedTopics
    .map(t => TOPIC_LABELS[t as keyof typeof TOPIC_LABELS] ?? null)
    .filter((s): s is string => s !== null)
    .join('・') || '未指定主題'

  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left px-4 py-3 transition-colors duration-fast ease-apple hover:bg-bg-secondary focus-visible:outline-none focus-visible:bg-bg-secondary ${
        selected ? 'bg-accent/5' : ''
      }`}
    >
      <div className="flex items-center gap-3">
        <div className="shrink-0 w-12 text-center">
          <div className="text-[10px] text-text-tertiary">星期{getWeekdayLabel(order.date)}</div>
          <div className="text-sm font-semibold text-text-primary leading-none">
            {order.date.slice(8, 10)}
          </div>
          <div className="text-[10px] text-text-tertiary mt-0.5">{formatDateZhTW(order.date)}</div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-sm text-text-primary truncate">{topicSummary}</span>
          </div>
          <div className="flex items-center gap-2 mt-1 text-[11px] text-text-tertiary">
            <span>出餐 {order.deliveryTime}</span>
            <StatusText status={order.status} locked={locked} />
          </div>
        </div>
        <div className="shrink-0 flex items-center gap-1">
          <StatusIcon status={order.status} locked={locked} />
          <PlayHint selected={selected} />
        </div>
      </div>
    </button>
  )
}

function StatusText({ status, locked }: { readonly status: DailyOrderStatus; readonly locked: boolean }) {
  if (status === 'played') return <span className="text-success">已播放</span>
  if (locked) return <span className="text-warning">已鎖定</span>
  if (status === 'queued') return <span className="text-accent">已排入</span>
  return <span className="text-accent">已送出</span>
}

function StatusIcon({ status, locked }: { readonly status: DailyOrderStatus; readonly locked: boolean }) {
  if (status === 'played') return <CheckCircle2 size={14} className="text-success" aria-hidden />
  if (locked) return <Lock size={14} className="text-warning" aria-hidden />
  if (status === 'queued') return <CheckCircle2 size={14} className="text-accent" aria-hidden />
  return <CheckCircle2 size={14} className="text-accent" aria-hidden />
}

function PlayHint({ selected }: { readonly selected: boolean }) {
  if (!selected) return null
  return <Play size={12} className="text-accent" aria-hidden />
}