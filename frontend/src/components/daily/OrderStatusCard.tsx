import { AnimatePresence, motion } from 'framer-motion'
import { Sparkles, Loader2 } from 'lucide-react'
import { Button } from '../primitives'
import { useSprings } from '../../lib/motion'
import type { DailyOrder } from '../../api'

interface OrderStatusCardProps {
  readonly activeOrder: DailyOrder | null
  readonly onOrderNew: () => void
  readonly onCancel: (id: string) => void
}

/** 頁面主角：永遠只顯示「目前這一餐」的狀態機（插槽是主軸，不是日期）。
 *  只有兩態——GET /active 只回 pending/queued，生成完成（ready）當下就會
 *  從 active 消失、出現在歷史紀錄裡，不需要在這張卡上等使用者播放完。
 *  切態走 spring crossfade（apple-design §4：從當前畫面值出發，不是 CSS fade）。 */
export function OrderStatusCard({ activeOrder, onOrderNew, onCancel }: OrderStatusCardProps) {
  const springs = useSprings()
  const state = activeOrder ? 'generating' : 'empty'

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
          {state === 'generating' && activeOrder && (
            <GeneratingPanel order={activeOrder} onCancel={onCancel} />
          )}
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
          通常幾分鐘內完成，完成後這裡會通知你
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
