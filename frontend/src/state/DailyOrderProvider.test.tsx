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
import type { DailyOrder, DailyOrderInput } from '../api'

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
const listOrderHistory = vi.fn(async (_limit?: number, _before?: string): Promise<readonly DailyOrder[]> => [])

vi.mock('../api', () => ({
  get api() {
    return {
      createDailyOrder,
      triggerGenerateJob,
      getActiveOrder,
      listOrderHistory,
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
  listOrderHistory.mockClear()
  // 預設行為：沒有進行中訂單、沒有歷史，createDailyOrder 回傳呼叫端指定的 order
  getActiveOrder.mockResolvedValue(null)
  listOrderHistory.mockResolvedValue([])
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
})

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
