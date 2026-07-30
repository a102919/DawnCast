import { useState } from 'react'
import { toast } from 'sonner'
import { AppError } from '../api'
import { useDailyOrder } from '../state'
import { OrderStatusCard } from '../components/daily/OrderStatusCard'
import { DailyOrderForm, type DailyOrderFormSubmitResult } from '../components/daily/DailyOrderForm'
import { OrderHistoryList } from '../components/daily/OrderHistoryList'
import { Sheet } from '../components/primitives'

export function DailyRoute() {
  const { activeOrder, history, createOrder, cancelOrder, loadMoreHistory } = useDailyOrder()
  const [openSheet, setOpenSheet] = useState(false)
  const [busy, setBusy] = useState(false)

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

      {/* 頁面主角：目前這一餐的狀態機 */}
      <OrderStatusCard
        activeOrder={activeOrder}
        onOrderNew={() => setOpenSheet(true)}
        onCancel={id => void handleCancel(id)}
      />

      {/* 歷史紀錄 */}
      <OrderHistoryList history={history} onLoadMore={() => void loadMoreHistory()} />

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
