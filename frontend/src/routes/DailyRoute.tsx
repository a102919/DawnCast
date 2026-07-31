import { useState } from 'react'
import { toast } from 'sonner'
import { AppError } from '../api'
import { useDailyOrder } from '../state'
import { OrderStatusCard } from '../components/daily/OrderStatusCard'
import { DailyOrderForm, type DailyOrderFormSubmitResult } from '../components/daily/DailyOrderForm'
import { OrderHistoryList } from '../components/daily/OrderHistoryList'
import { Sheet } from '../components/primitives'
import { ErrorBanner } from '../components/primitives/ErrorBanner'

export function DailyRoute() {
  const { activeOrder, history, error, createOrder, cancelOrder, refresh } = useDailyOrder()
  const [openSheet, setOpenSheet] = useState(false)
  const [busy, setBusy] = useState(false)

  // 目前這一餐：active（pending/queued）優先；插槽空了就看最近一筆是不是
  // ready/expired——這兩態要在主卡上呈現（播放入口／重新點播），played 則
  // 代表這一餐完結，回到空插槽。
  const latest = history[0]
  const latestOrder =
    activeOrder ??
    (latest && (latest.status === 'ready' || latest.status === 'expired') ? latest : null)

  const handleSubmit = async (result: DailyOrderFormSubmitResult) => {
    setBusy(true)
    try {
      await createOrder({
        selectedTopics: result.selectedTopics,
        ...(result.specificRequest ? { specificRequest: result.specificRequest } : {}),
        entryMode: result.entryMode,
        lengthTier: result.lengthTier,
      })
      toast.success('已送出，開始生成')
      setOpenSheet(false)
    } catch (err) {
      toast.error(err instanceof AppError ? err.message : '發生錯誤，請重試')
    } finally {
      setBusy(false)
    }
  }

  const handleCancel = async (id: string) => {
    setBusy(true)
    try {
      await cancelOrder(id)
      toast.success('已取消，可以重新點一份')
    } catch {
      toast.error('取消失敗，請重試')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-6">
      {/* 標頭：拿掉裝飾性圖示徽章——跟下方 OrderStatusCard 的狀態圖示重複，
          不帶額外資訊（apple-design §16 simplicity：每個元素都要有存在理由）。 */}
      <div>
        <h1 className="text-xl font-semibold text-text-primary">點播</h1>
        <p className="text-xs text-text-tertiary mt-0.5">隨時點，一次一份</p>
      </div>

      {/* 載入／輪詢失敗：跟「沒訂單」分開呈現，並給顯式重試入口 */}
      {error !== null && (
        <ErrorBanner variant="inline" message={error} onRetry={() => void refresh()} />
      )}

      {/* 頁面主角：目前這一餐的狀態機 */}
      <OrderStatusCard
        latestOrder={latestOrder}
        onOrderNew={() => setOpenSheet(true)}
        onCancel={id => void handleCancel(id)}
      />

      {/* 歷史紀錄（資料與解析快取直接吃 DailyOrderProvider） */}
      <OrderHistoryList />

      {/* 點餐表單：只在使用者主動點 CTA 時開啟，每次打開都是建新單 */}
      <Sheet
        isOpen={openSheet}
        onClose={() => setOpenSheet(false)}
        variant="bottom"
        ariaLabelledBy="daily-order-sheet-title"
      >
        <DailyOrderForm
          key={openSheet ? 'open' : 'closed'}
          busy={busy}
          onSubmit={r => void handleSubmit(r)}
        />
      </Sheet>
    </div>
  )
}
