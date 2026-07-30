import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type DailyOrder, type DailyOrderInput } from '../api'
import { DailyOrderContext, type DailyOrderContextValue } from './dailyOrderContextValue'

const HISTORY_PAGE_SIZE = 20

export function DailyOrderProvider({ children }: { readonly children: ReactNode }) {
  const [activeOrder, setActiveOrder] = useState<DailyOrder | null>(null)
  const [history, setHistory] = useState<readonly DailyOrder[]>([])
  const [historyExhausted, setHistoryExhausted] = useState(false)

  const loadActive = useCallback(async (): Promise<void> => {
    try {
      setActiveOrder(await api.getActiveOrder())
    } catch (err) {
      console.warn('[daily-order] load active failed', err)
    }
  }, [])

  const loadHistory = useCallback(async (): Promise<void> => {
    try {
      const list = await api.listOrderHistory(HISTORY_PAGE_SIZE)
      setHistory(list)
      setHistoryExhausted(list.length < HISTORY_PAGE_SIZE)
    } catch (err) {
      console.warn('[daily-order] load history failed', err)
    }
  }, [])

  const refresh = useCallback(async (): Promise<void> => {
    await Promise.all([loadActive(), loadHistory()])
  }, [loadActive, loadHistory])

  // 初次掛載跑一次載入。mounted ref 確保 StrictMode 雙 mount 只觸發一次。
  const mountedRef = useRef<boolean>(false)
  useEffect(() => {
    if (mountedRef.current) return
    mountedRef.current = true
    void refresh()
  }, [refresh])

  const loadMoreHistory = useCallback(async (): Promise<void> => {
    if (historyExhausted) return
    const last = history.at(-1)
    if (!last) return
    try {
      const more = await api.listOrderHistory(HISTORY_PAGE_SIZE, last.createdAt)
      setHistory(prev => [...prev, ...more])
      if (more.length < HISTORY_PAGE_SIZE) setHistoryExhausted(true)
    } catch (err) {
      console.warn('[daily-order] load more history failed', err)
    }
  }, [history, historyExhausted])

  const createOrder = useCallback(async (input: DailyOrderInput): Promise<DailyOrder> => {
    const created = await api.createDailyOrder(input)
    setActiveOrder(created)
    // fire-and-forget：失敗僅 log，不影響 createOrder 回傳值。
    // dawncast-order-reconcile（每 5 分鐘）會撿走卡在 pending 太久的訂單重放觸發。
    void api.triggerGenerateJob(created.id).catch(err => {
      console.warn('[daily-order] trigger generate failed', err)
    })
    return created
  }, [])

  const cancelOrder = useCallback(async (id: string): Promise<void> => {
    await api.deleteDailyOrder(id)
    setActiveOrder(prev => (prev?.id === id ? null : prev))
  }, [])

  const markPlayed = useCallback(async (id: string): Promise<DailyOrder | null> => {
    const playedAt = new Date().toISOString()
    const updated = await api.markOrderPlayed(id, playedAt)
    if (!updated) return null
    setActiveOrder(prev => (prev?.id === id ? null : prev))
    setHistory(prev => [updated, ...prev.filter(o => o.id !== id)])
    return updated
  }, [])

  const value: DailyOrderContextValue = {
    activeOrder,
    history,
    createOrder,
    cancelOrder,
    markPlayed,
    loadMoreHistory,
    refresh,
  }

  return (
    <DailyOrderContext.Provider value={value}>
      {children}
    </DailyOrderContext.Provider>
  )
}
