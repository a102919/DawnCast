import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type DailyOrder, type DailyOrderInput } from '../api'
import type { Episode } from '../types/episode'
import {
  DailyOrderContext,
  type DailyOrderContextValue,
  type OrderEpisodeEntry,
} from './dailyOrderContextValue'

const HISTORY_PAGE_SIZE = 20

// 輪詢 backoff：2/4/8/16s，30 次後降速 60s 慢輪（不停死——訂單生成可能
// 超過 backoff 涵蓋的時間，停死會回到「永遠顯示生成中」的老 bug）。
const POLL_DELAYS_MS = [2_000, 4_000, 8_000, 16_000] as const
const SLOW_POLL_AFTER = 30
const SLOW_POLL_MS = 60_000

function delayFor(pollCount: number): number {
  if (pollCount >= SLOW_POLL_AFTER) return SLOW_POLL_MS
  return POLL_DELAYS_MS[Math.min(pollCount, POLL_DELAYS_MS.length - 1)]!
}

/** 點播訂單的唯一事實來源：active 訂單、輪詢、歷史、訂單→集數解析快取
 *  全部收在這裡；頁面元件只消費，不各自輪詢、不各自建快取。 */
export function DailyOrderProvider({ children }: { readonly children: ReactNode }) {
  const [activeOrder, setActiveOrder] = useState<DailyOrder | null>(null)
  const [history, setHistory] = useState<readonly DailyOrder[]>([])
  const [historyExhausted, setHistoryExhausted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [orderEpisodes, setOrderEpisodes] = useState<ReadonlyMap<string, OrderEpisodeEntry>>(
    new Map(),
  )

  // 解析快取的同步鏡像＋in-flight 去重：state 更新是非同步的，連續呼叫
  // resolveOrderEpisode 若只看 state 會重複打同一筆訂單的 API。
  const entriesRef = useRef<Map<string, OrderEpisodeEntry>>(new Map())
  const inflightRef = useRef<Map<string, Promise<Episode | null>>>(new Map())

  const setEntry = useCallback((orderId: string, entry: OrderEpisodeEntry): void => {
    entriesRef.current = new Map(entriesRef.current).set(orderId, entry)
    setOrderEpisodes(entriesRef.current)
  }, [])

  const resolveOrderEpisode = useCallback(
    async (orderId: string): Promise<Episode | null> => {
      const existing = entriesRef.current.get(orderId)
      if (existing?.state === 'done') return existing.episode
      // failed 不自動重試：這正是舊版 OrderHistoryList 無限請求迴圈的根因
      //（解析失敗 → effect 重跑 → 再打一輪）。重試入口只有顯式 refresh()。
      if (existing?.state === 'failed') return null
      const inflight = inflightRef.current.get(orderId)
      if (inflight) return inflight

      setEntry(orderId, { state: 'loading' })
      const request = api
        .getDeliveredEpisode(orderId)
        .then(episode => {
          setEntry(orderId, { state: 'done', episode })
          return episode
        })
        .catch((err: unknown) => {
          console.warn('[daily-order] 解析訂單集數失敗', orderId, err)
          setEntry(orderId, { state: 'failed' })
          return null
        })
        .finally(() => {
          inflightRef.current.delete(orderId)
        })
      inflightRef.current.set(orderId, request)
      return request
    },
    [setEntry],
  )

  const loadActive = useCallback(async (): Promise<void> => {
    try {
      setActiveOrder(await api.getActiveOrder())
      setError(null)
    } catch (err) {
      console.warn('[daily-order] load active failed', err)
      setError('訂單載入失敗，請稍後重試')
    }
  }, [])

  const loadHistory = useCallback(async (): Promise<void> => {
    try {
      const list = await api.listOrderHistory(HISTORY_PAGE_SIZE)
      setHistory(list)
      setHistoryExhausted(list.length < HISTORY_PAGE_SIZE)
    } catch (err) {
      console.warn('[daily-order] load history failed', err)
      setError('訂單載入失敗，請稍後重試')
    }
  }, [])

  const refresh = useCallback(async (): Promise<void> => {
    // 清 failed 解析快取：refresh 是使用者顯式重試的唯一入口
    const cleaned = new Map(
      [...entriesRef.current].filter(([, entry]) => entry.state !== 'failed'),
    )
    entriesRef.current = cleaned
    setOrderEpisodes(cleaned)
    await Promise.all([loadActive(), loadHistory()])
  }, [loadActive, loadHistory])

  // 初次掛載跑一次載入。mounted ref 確保 StrictMode 雙 mount 只觸發一次。
  const mountedRef = useRef<boolean>(false)
  useEffect(() => {
    if (mountedRef.current) return
    mountedRef.current = true
    void refresh()
  }, [refresh])

  // 輪詢掛在 Provider（app 層級）：只要有進行中訂單就輪，不綁任何頁面——
  // 舊版只有首頁輪詢，使用者停在 /daily 會永遠看到「生成中」。
  // deps 只吃 activeId（primitive）：pending→queued 的狀態更新不重置 backoff。
  const activeId =
    activeOrder && (activeOrder.status === 'pending' || activeOrder.status === 'queued')
      ? activeOrder.id
      : null

  useEffect(() => {
    if (activeId === null) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let polls = 0

    const schedule = (): void => {
      if (cancelled) return
      timer = setTimeout(() => {
        void tick()
      }, delayFor(polls))
    }

    const tick = async (): Promise<void> => {
      if (cancelled) return
      // 背景分頁不打；回到前景由 visibilitychange 立即補一發
      if (document.hidden) return
      polls += 1
      try {
        const latest = await api.getDailyOrder(activeId)
        if (cancelled) return
        if (latest === null) {
          // 訂單消失（他裝置取消）→ 插槽清空
          setActiveOrder(cur => (cur?.id === activeId ? null : cur))
          return
        }
        if (latest.status === 'ready' || latest.status === 'expired') {
          setActiveOrder(cur => (cur?.id === activeId ? null : cur))
          setHistory(prev => [latest, ...prev.filter(o => o.id !== latest.id)])
          if (latest.status === 'ready') void resolveOrderEpisode(latest.id)
          return
        }
        // pending/queued：更新進度（pending→queued），繼續輪
        setActiveOrder(cur => (cur?.id === activeId ? latest : cur))
      } catch (err) {
        console.warn('[daily-order] 輪詢訂單狀態失敗', err)
      }
      schedule()
    }

    const onVisibilityChange = (): void => {
      if (document.hidden) return
      if (timer !== undefined) clearTimeout(timer)
      void tick()
    }

    document.addEventListener('visibilitychange', onVisibilityChange)
    schedule()
    return () => {
      cancelled = true
      if (timer !== undefined) clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [activeId, resolveOrderEpisode])

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
    setError(null)
    // fire-and-forget：失敗不影響 createOrder 回傳值——後端
    // dawncast-order-reconcile（每 5 分鐘）會撿走卡在 pending 的訂單重放，
    // 但要讓使用者知道「會慢一點」，不能只有 console.warn。
    void api.triggerGenerateJob(created.id).catch(err => {
      console.warn('[daily-order] trigger generate failed', err)
      setError('已送出，稍後自動開始生成')
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

  const value: DailyOrderContextValue = useMemo(
    () => ({
      activeOrder,
      history,
      historyExhausted,
      error,
      orderEpisodes,
      resolveOrderEpisode,
      createOrder,
      cancelOrder,
      markPlayed,
      loadMoreHistory,
      refresh,
    }),
    [
      activeOrder,
      history,
      historyExhausted,
      error,
      orderEpisodes,
      resolveOrderEpisode,
      createOrder,
      cancelOrder,
      markPlayed,
      loadMoreHistory,
      refresh,
    ],
  )

  return (
    <DailyOrderContext.Provider value={value}>
      {children}
    </DailyOrderContext.Provider>
  )
}
