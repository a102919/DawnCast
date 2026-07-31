// @vitest-environment happy-dom
// DailyOrderProvider 測試（隨時點餐：送出後立即觸發生成 pipeline）。

// React 19 對 act() 的環境感知旗標，沒設會跳 warn；不影響測試通過但很吵。
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
//
// 重點：把「createDailyOrder → triggerGenerateJob」這個呼叫鏈釘進 CI，否則
// DailyOrderProvider.createOrder 重構時若漏掉 triggerGenerateJob，送出後
// dawncast-order-reconcile 兜底前使用者什麼都收不到。
//
// 不裝 @testing-library/react，直接用 react-dom/client.createRoot +
// happy-dom 提供的 window/document 就夠（本檔案專注測行為，不驗 DOM）。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, useEffect, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { DailyOrderProvider } from './DailyOrderProvider'
import { useDailyOrder } from './useDailyOrder'
import type { DailyOrderContextValue } from './dailyOrderContextValue'
import type { DailyOrder, DailyOrderInput } from '../api'
import type { Episode } from '../types/episode'

const baseOrder = (overrides: Partial<DailyOrder> = {}): DailyOrder => ({
  id: 'order-1',
  date: '2026-07-16',
  selectedTopics: ['tech'],
  status: 'pending',
  deliveryTime: '07:00',
  createdAt: '2026-07-16T00:00:00Z',
  updatedAt: '2026-07-16T00:00:00Z',
  entryMode: 'topic',
  lengthTier: 'medium',
  ready: false,
  ...overrides,
})

// Mock api 模組：spyOn 真物件太繞，直接替換整個 export。
const createDailyOrder = vi.fn(async (_input: DailyOrderInput): Promise<DailyOrder> => baseOrder())
const triggerGenerateJob = vi.fn(async (_orderId: string) => undefined)
const getActiveOrder = vi.fn(async (): Promise<DailyOrder | null> => null)
const getDailyOrder = vi.fn(async (_id: string): Promise<DailyOrder | null> => null)
const listOrderHistory = vi.fn(async (_limit?: number, _before?: string): Promise<readonly DailyOrder[]> => [])
const getDeliveredEpisode = vi.fn(async (_orderId: string): Promise<Episode | null> => null)
const markOrderPlayed = vi.fn(async (_id: string, _playedAt: string): Promise<DailyOrder | null> => null)
const deleteDailyOrder = vi.fn(async (_id: string) => undefined)

vi.mock('../api', () => ({
  get api() {
    return {
      createDailyOrder,
      triggerGenerateJob,
      getActiveOrder,
      getDailyOrder,
      listOrderHistory,
      getDeliveredEpisode,
      markOrderPlayed,
      deleteDailyOrder,
    }
  },
}))

// 包一個 hook tester 把 context 裡的 createOrder 暴露到外部供 await 呼叫。
function CaptureCreateOrder({ onReady }: { onReady: (co: (input: DailyOrderInput) => Promise<DailyOrder>) => void }) {
  const ctx = useDailyOrder()
  // createOrder 來自 useCallback，引用穩定；useEffect 只在 mount 跑一次就夠，
  // 不需要再 force re-render。onReady 由 renderProvider 同步指定，不會變。
  useEffect(() => {
    onReady(ctx.createOrder)
  }, [ctx.createOrder, onReady])
  return null
}

function Wrapper({ children }: { readonly children: ReactNode }) {
  return <DailyOrderProvider>{children}</DailyOrderProvider>
}

async function renderProvider(): Promise<{
  createOrder: (input: DailyOrderInput) => Promise<DailyOrder>
  root: Root
  container: HTMLDivElement
}> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  let createOrderRef: ((input: DailyOrderInput) => Promise<DailyOrder>) | null = null

  await act(async () => {
    root.render(
      <Wrapper>
        <CaptureCreateOrder onReady={co => { createOrderRef = co }} />
      </Wrapper>,
    )
  })

  if (!createOrderRef) throw new Error('createOrder 尚未就緒（CaptureCreateOrder useEffect 沒跑）')
  return { createOrder: createOrderRef, root, container }
}

// 把每個測試用過的 root 收起來，在 afterEach 統一 unmount + 清 DOM，
// 避免漏寫 unmount 造成 happy-dom 留節點污染下一個測試 + act warn。
const pendingRoots: Root[] = []

beforeEach(() => {
  createDailyOrder.mockClear()
  triggerGenerateJob.mockClear()
  getActiveOrder.mockClear()
  getDailyOrder.mockClear()
  listOrderHistory.mockClear()
  getDeliveredEpisode.mockClear()
  markOrderPlayed.mockClear()
  deleteDailyOrder.mockClear()
  // 預設行為：沒有進行中訂單、沒有歷史，createDailyOrder 回傳呼叫端指定的 order
  getActiveOrder.mockResolvedValue(null)
  getDailyOrder.mockResolvedValue(null)
  listOrderHistory.mockResolvedValue([])
  getDeliveredEpisode.mockResolvedValue(null)
  createDailyOrder.mockImplementation(async (input: DailyOrderInput) =>
    baseOrder({ selectedTopics: [...input.selectedTopics] }))
  triggerGenerateJob.mockResolvedValue(undefined)
  // 抑制觸發呼叫的 console.warn（測試錯誤路徑時顯然會跑）
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
  vi.useRealTimers()
})

// 捕捉整個 context value（輪詢／解析快取測試用），每次 render 更新最新值。
function CaptureContext({ onCtx }: { onCtx: (ctx: DailyOrderContextValue) => void }) {
  const ctx = useDailyOrder()
  onCtx(ctx)
  return null
}

async function renderProviderWithContext(): Promise<{
  ctx: () => DailyOrderContextValue
  root: Root
}> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  let latest: DailyOrderContextValue | null = null
  await act(async () => {
    root.render(
      <Wrapper>
        <CaptureContext onCtx={c => { latest = c }} />
      </Wrapper>,
    )
  })
  if (!latest) throw new Error('context 尚未就緒')
  return { ctx: () => latest!, root }
}

describe('DailyOrderProvider.createOrder 觸發鏈', () => {
  it('呼叫順序：createDailyOrder → triggerGenerateJob', async () => {
    const { createOrder, root } = await renderProvider()
    pendingRoots.push(root)

    const input: DailyOrderInput = {
      selectedTopics: ['tech'],
      specificRequest: 'AI',
      entryMode: 'topic',
      lengthTier: 'medium',
    }

    await act(async () => {
      await createOrder(input)
    })

    expect(createDailyOrder).toHaveBeenCalledTimes(1)
    expect(triggerGenerateJob).toHaveBeenCalledTimes(1)

    // 呼叫順序檢查：create 必須先於 trigger。
    const createOrderCall = createDailyOrder.mock.invocationCallOrder[0]!
    const triggerOrder = triggerGenerateJob.mock.invocationCallOrder[0]!
    expect(createOrderCall).toBeLessThan(triggerOrder)
  })

  it('triggerGenerateJob 收到的參數等於 createDailyOrder 回傳的 id', async () => {
    createDailyOrder.mockResolvedValueOnce(baseOrder({ id: 'order-42' }))
    const { createOrder, root } = await renderProvider()
    pendingRoots.push(root)

    await act(async () => {
      await createOrder({ selectedTopics: ['tech'] })
    })

    expect(triggerGenerateJob).toHaveBeenCalledWith('order-42')
  })

  it('triggerGenerateJob reject 時 createOrder 仍 resolve（fire-and-forget 不打斷）', async () => {
    const failure = new Error('simulated network 500')
    triggerGenerateJob.mockRejectedValueOnce(failure)

    const { createOrder, root } = await renderProvider()
    pendingRoots.push(root)

    // 用微任務 + act 包起來，確保 fire-and-forget 的 promise 真的被吃下 catch。
    const captured = await act(async () => createOrder({ selectedTopics: ['tech'] }))
    // 再讓 microtask 清乾淨（catch handler 會跑）
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // createOrder 仍要回 saved order，不 throw、不變 undefined
    expect(captured).not.toBeNull()
    expect(captured.selectedTopics).toEqual(['tech'])

    // console.warn 應該被呼叫過（失敗有跡可循）
    expect(console.warn).toHaveBeenCalled()
  })

  it('createDailyOrder 失敗時 createOrder 仍要 reject（這個不是 fire-and-forget）', async () => {
    createDailyOrder.mockRejectedValueOnce(new Error('POST /daily-orders 409'))

    const { createOrder, root } = await renderProvider()
    pendingRoots.push(root)

    await act(async () => {
      await expect(
        createOrder({ selectedTopics: ['tech'] }),
      ).rejects.toThrow('POST /daily-orders 409')
    })

    // trigger 不應被呼叫（前面已經炸了）
    expect(triggerGenerateJob).not.toHaveBeenCalled()
  })
})

describe('DailyOrderProvider 輪詢（app 層級，不綁頁面）', () => {
  it('active 訂單輪詢到 ready：移出 active、進 history、預熱集數快取', async () => {
    vi.useFakeTimers()
    getActiveOrder.mockResolvedValue(baseOrder({ status: 'queued' }))
    getDailyOrder.mockResolvedValue(baseOrder({ status: 'ready', ready: true }))

    const { ctx, root } = await renderProviderWithContext()
    pendingRoots.push(root)
    expect(ctx().activeOrder?.id).toBe('order-1')

    // 第一個 backoff tick（2s）命中 ready
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })

    expect(getDailyOrder).toHaveBeenCalledWith('order-1')
    expect(ctx().activeOrder).toBeNull()
    expect(ctx().history[0]?.id).toBe('order-1')
    expect(ctx().history[0]?.status).toBe('ready')
    // ready 時預熱解析快取
    expect(getDeliveredEpisode).toHaveBeenCalledWith('order-1')
  })

  it('輪詢到 expired：一樣移入 history，但不預熱集數快取', async () => {
    vi.useFakeTimers()
    getActiveOrder.mockResolvedValue(baseOrder({ status: 'queued' }))
    getDailyOrder.mockResolvedValue(baseOrder({ status: 'expired' }))

    const { ctx, root } = await renderProviderWithContext()
    pendingRoots.push(root)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000)
    })

    expect(ctx().activeOrder).toBeNull()
    expect(ctx().history[0]?.status).toBe('expired')
    expect(getDeliveredEpisode).not.toHaveBeenCalled()
  })

  it('分頁在背景（document.hidden）時暫停，回前景立即補一發', async () => {
    vi.useFakeTimers()
    getActiveOrder.mockResolvedValue(baseOrder({ status: 'queued' }))
    getDailyOrder.mockResolvedValue(baseOrder({ status: 'queued' }))
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })

    try {
      const { root } = await renderProviderWithContext()
      pendingRoots.push(root)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000)
      })
      expect(getDailyOrder).not.toHaveBeenCalled()

      // 回前景：visibilitychange 立即補一發
      Object.defineProperty(document, 'hidden', { configurable: true, value: false })
      await act(async () => {
        document.dispatchEvent(new Event('visibilitychange'))
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(getDailyOrder).toHaveBeenCalledTimes(1)
    } finally {
      delete (document as { hidden?: boolean }).hidden
    }
  })
})

describe('DailyOrderProvider.resolveOrderEpisode 解析快取', () => {
  it('失敗後不自動重試（釘死舊版 OrderHistoryList 的無限請求迴圈）', async () => {
    getDeliveredEpisode.mockRejectedValueOnce(new Error('500'))

    const { ctx, root } = await renderProviderWithContext()
    pendingRoots.push(root)

    const first = await act(async () => ctx().resolveOrderEpisode('o-x'))
    const second = await act(async () => ctx().resolveOrderEpisode('o-x'))

    expect(first).toBeNull()
    expect(second).toBeNull()
    expect(getDeliveredEpisode).toHaveBeenCalledTimes(1)
    expect(ctx().orderEpisodes.get('o-x')?.state).toBe('failed')
  })

  it('成功結果進快取，重複呼叫不重打；in-flight 併發去重', async () => {
    const episode = { id: 'ep-1', title: 'T' } as Episode
    getDeliveredEpisode.mockResolvedValue(episode)

    const { ctx, root } = await renderProviderWithContext()
    pendingRoots.push(root)

    const [a, b] = await act(async () =>
      Promise.all([ctx().resolveOrderEpisode('o-y'), ctx().resolveOrderEpisode('o-y')]))
    const c = await act(async () => ctx().resolveOrderEpisode('o-y'))

    expect(a?.id).toBe('ep-1')
    expect(b?.id).toBe('ep-1')
    expect(c?.id).toBe('ep-1')
    expect(getDeliveredEpisode).toHaveBeenCalledTimes(1)
  })

  it('refresh() 清除 failed 快取，之後可以重試', async () => {
    getDeliveredEpisode.mockRejectedValueOnce(new Error('500'))

    const { ctx, root } = await renderProviderWithContext()
    pendingRoots.push(root)

    await act(async () => ctx().resolveOrderEpisode('o-z'))
    expect(ctx().orderEpisodes.get('o-z')?.state).toBe('failed')

    await act(async () => ctx().refresh())
    expect(ctx().orderEpisodes.has('o-z')).toBe(false)

    getDeliveredEpisode.mockResolvedValueOnce({ id: 'ep-2', title: 'T2' } as Episode)
    const retried = await act(async () => ctx().resolveOrderEpisode('o-z'))
    expect(retried?.id).toBe('ep-2')
    expect(getDeliveredEpisode).toHaveBeenCalledTimes(2)
  })
})
