import { AnimatePresence, motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { Sparkles, Loader2, Play, Ban } from 'lucide-react'
import { Button } from '../primitives'
import { useSprings } from '../../lib/motion'
import type { DailyOrder } from '../../api'

interface OrderStatusCardProps {
  /** 目前這一餐：active（pending/queued）優先，否則最近一筆 ready/expired。 */
  readonly latestOrder: DailyOrder | null
  readonly onOrderNew: () => void
  readonly onCancel: (id: string) => void
}

type CardState = 'empty' | 'generating' | 'ready' | 'expired'

function resolveState(order: DailyOrder | null): CardState {
  if (!order) return 'empty'
  if (order.status === 'pending' || order.status === 'queued') return 'generating'
  if (order.status === 'ready') return 'ready'
  if (order.status === 'expired') return 'expired'
  return 'empty' // played：這一餐已完結，插槽空了
}

/** 頁面主角：永遠只顯示「目前這一餐」的狀態機（插槽是主軸，不是日期）。
 *  四態：empty（可點新單）／generating（pending/queued）／ready（生成完成，
 *  給播放入口）／expired（系統放棄，給重新點播入口）。
 *  切態走 spring crossfade（apple-design §4：從當前畫面值出發，不是 CSS fade）。 */
export function OrderStatusCard({ latestOrder, onOrderNew, onCancel }: OrderStatusCardProps) {
  const springs = useSprings()
  const state = resolveState(latestOrder)

  return (
    <section className="rounded-xl border border-border bg-bg-primary p-5 overflow-hidden">
      <AnimatePresence mode="wait">
        <motion.div
          key={state}
          initial={{ opacity: 0, y: 8, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1, transition: springs.gentle }}
          exit={{ opacity: 0, y: -8, scale: 0.98, transition: springs.gentle }}
        >
          {state === 'empty' && <EmptyPanel onOrderNew={onOrderNew} />}
          {state === 'generating' && latestOrder && (
            <GeneratingPanel order={latestOrder} onCancel={onCancel} />
          )}
          {state === 'ready' && latestOrder && (
            <ReadyPanel order={latestOrder} onOrderNew={onOrderNew} />
          )}
          {state === 'expired' && <ExpiredPanel onOrderNew={onOrderNew} />}
        </motion.div>
      </AnimatePresence>
    </section>
  )
}

function EmptyPanel({ onOrderNew }: { readonly onOrderNew: () => void }) {
  return (
    <div className="flex items-center gap-4">
      <div className="w-11 h-11 rounded-full bg-accent/10 flex items-center justify-center text-accent shrink-0">
        <Sparkles size={20} aria-hidden />
      </div>
      <div className="flex-1 min-w-0">
        <h2 className="text-headline tracking-headline leading-headline font-semibold text-text-primary">
          還沒有進行中的點播
        </h2>
        <p className="text-caption leading-caption text-text-tertiary mt-0.5">
          想聽點什麼都可以，送出後馬上開始生成
        </p>
      </div>
      <Button variant="primary" size="md" onClick={onOrderNew}>
        <Sparkles size={14} />
        現在點播
      </Button>
    </div>
  )
}

function GeneratingPanel({
  order,
  onCancel,
}: {
  readonly order: DailyOrder
  readonly onCancel: (id: string) => void
}) {
  // 只有 pending（還沒真正進 queue）才允許取消；queued 已經開始生成，
  // 後端會回 409（見 daily_orders.py DELETE，取消訂單的視窗很短暫）。
  const cancellable = order.status === 'pending'
  return (
    <div className="flex items-center gap-4">
      <div className="w-11 h-11 rounded-full bg-accent/10 flex items-center justify-center text-accent shrink-0">
        <Loader2 size={20} className="motion-safe:animate-spin" aria-hidden />
      </div>
      <div className="flex-1 min-w-0">
        <h2 className="text-headline tracking-headline leading-headline font-semibold text-text-primary">
          這集正在生成中
        </h2>
        <p className="text-caption leading-caption text-text-tertiary mt-0.5">
          通常幾分鐘內完成，完成後這裡會直接更新
        </p>
        {cancellable && (
          <button
            type="button"
            onClick={() => onCancel(order.id)}
            className="mt-1 text-[11px] text-text-tertiary hover:text-danger transition-colors duration-fast"
          >
            取消這集
          </button>
        )}
      </div>
      <div className="text-right shrink-0">
        <Button variant="secondary" size="md" disabled>
          點播下一集
        </Button>
        <p className="text-[11px] text-text-tertiary mt-1 max-w-[9rem]">
          完成後才能點下一集
        </p>
      </div>
    </div>
  )
}

function ReadyPanel({
  order,
  onOrderNew,
}: {
  readonly order: DailyOrder
  readonly onOrderNew: () => void
}) {
  return (
    <div className="flex items-center gap-4">
      <div className="w-11 h-11 rounded-full bg-success/10 flex items-center justify-center text-success shrink-0">
        <Play size={20} aria-hidden />
      </div>
      <div className="flex-1 min-w-0">
        <h2 className="text-headline tracking-headline leading-headline font-semibold text-text-primary">
          這集做好了
        </h2>
        <p className="text-caption leading-caption text-text-tertiary mt-0.5">
          隨時可以開始聽；下一集也解鎖了
        </p>
        <button
          type="button"
          onClick={onOrderNew}
          className="mt-1 text-[11px] text-text-tertiary hover:text-accent transition-colors duration-fast"
        >
          點播下一集
        </button>
      </div>
      <Link to={`/player?orderId=${order.id}`} className="shrink-0">
        <Button variant="primary" size="md">
          <Play size={14} fill="currentColor" />
          立即收聽
        </Button>
      </Link>
    </div>
  )
}

function ExpiredPanel({ onOrderNew }: { readonly onOrderNew: () => void }) {
  return (
    <div className="flex items-center gap-4">
      <div className="w-11 h-11 rounded-full bg-bg-secondary flex items-center justify-center text-text-tertiary shrink-0">
        <Ban size={20} aria-hidden />
      </div>
      <div className="flex-1 min-w-0">
        <h2 className="text-headline tracking-headline leading-headline font-semibold text-text-primary">
          這集生成失敗，已放棄
        </h2>
        <p className="text-caption leading-caption text-text-tertiary mt-0.5">
          抱歉讓你久等了，換個主題或再點一次試試
        </p>
      </div>
      <Button variant="primary" size="md" onClick={onOrderNew}>
        <Sparkles size={14} />
        重新點播
      </Button>
    </div>
  )
}
