import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, type DailyOrder, type DailyOrderInput } from '../api'
import { DailyOrderContext, type DailyOrderContextValue } from './dailyOrderContextValue'
import { DEFAULT_DELIVERY_TIME, addDays, previousNDays, toIsoDate } from '../lib/dailyOrderDate'

const HISTORY_DAYS = 30
const FORWARD_DAYS = 7

function getTodayDate(): string {
  return toIsoDate(new Date())
}

export function DailyOrderProvider({ children }: { readonly children: ReactNode }) {
  const todayDate = useMemo(() => getTodayDate(), [])
  const [orders, setOrders] = useState<ReadonlyMap<string, DailyOrder>>(new Map())

  useEffect(() => {
    const fromDate = previousNDays(todayDate, HISTORY_DAYS).at(-1) ?? todayDate
    const toDate = addDays(todayDate, FORWARD_DAYS - 1)
    void api.listDailyOrders(fromDate, toDate).then(list => {
      const map = new Map<string, DailyOrder>()
      for (const o of list) map.set(o.date, o)
      setOrders(map)
    })
  }, [todayDate])

  const getOrder = useCallback(
    (date: string): DailyOrder | null => orders.get(date) ?? null,
    [orders],
  )

  const setOrder = useCallback(
    async (date: string, input: DailyOrderInput): Promise<DailyOrder> => {
      const now = new Date().toISOString()
      const previous = orders.get(date)
      // Phase 4：舊 localStorage 訂單沒 entryMode/lengthTier，補預設後才不會帶 undefined
      // 進 wire（後端會 422）。沿用舊值 > input > 預設值的優先序，與 deliveryTime 邏輯一致。
      const full: DailyOrder = {
        date,
        selectedTopics: [...input.selectedTopics],
        ...(input.specificRequest !== undefined && input.specificRequest !== ''
          ? { specificRequest: input.specificRequest }
          : {}),
        status: input.status ?? previous?.status ?? 'pending',
        deliveryTime: input.deliveryTime || DEFAULT_DELIVERY_TIME,
        createdAt: previous?.createdAt ?? now,
        updatedAt: now,
        entryMode: input.entryMode ?? previous?.entryMode ?? 'topic',
        lengthTier: input.lengthTier ?? previous?.lengthTier ?? 'medium',
      }
      const saved = await api.saveDailyOrder(full)
      await api.setLastOrderDate(date)
      setOrders(prev => {
        const next = new Map(prev)
        next.set(date, saved)
        return next
      })
      return saved
    },
    [orders],
  )

  const deleteOrder = useCallback(async (date: string): Promise<void> => {
    await api.deleteDailyOrder(date)
    setOrders(prev => {
      if (!prev.has(date)) return prev
      const next = new Map(prev)
      next.delete(date)
      return next
    })
  }, [])

  const markPlayed = useCallback(async (date: string): Promise<DailyOrder | null> => {
    const previous = orders.get(date)
    if (!previous) return null
    if (previous.status === 'played') return previous
    const playedAt = new Date().toISOString()
    const updated = await api.markOrderPlayed(date, playedAt)
    if (!updated) return null
    setOrders(prev => {
      const next = new Map(prev)
      next.set(date, updated)
      return next
    })
    return updated
  }, [orders])

  const value: DailyOrderContextValue = {
    todayDate,
    orders,
    getOrder,
    setOrder,
    deleteOrder,
    markPlayed,
  }

  return (
    <DailyOrderContext.Provider value={value}>
      {children}
    </DailyOrderContext.Provider>
  )
}