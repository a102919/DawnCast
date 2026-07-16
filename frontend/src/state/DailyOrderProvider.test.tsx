// @vitest-environment happy-dom
// DailyOrderProvider 測試（T1：前端送訂單後觸發 pipeline）。

// React 19 對 act() 的環境感知旗標，沒設會跳 warn；不影響測試通過但很吵。
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
//
// 重點：把「saveDailyOrder → setLastOrderDate → triggerGenerateJob」這個呼叫鏈
// 釘進 CI，否則 DailyOrderProvider.setOrder 重構時若漏掉 triggerGenerateJob，
// T1 會靜默失效（22:00 collect_open cron 兜底前使用者什麼都收不到）。
//
// 不裝 @testing-library/react，直接用 react-dom/client.createRoot +
// happy-dom 提供的 window/document 就夠（本檔案專注測行為，不驗 DOM）。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, useEffect, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { DailyOrderProvider } from './DailyOrderProvider'
import { useDailyOrder } from './useDailyOrder'
import type { DailyOrder, DailyOrderInput } from '../api'

// Mock api 模組：spyOn 真物件太繞，直接替換整個 export。
const saveDailyOrder = vi.fn(async (order: DailyOrder) => order)
const setLastOrderDate = vi.fn(async (_date: string) => undefined)
const triggerGenerateJob = vi.fn(async (_date: string) => undefined)
const listDailyOrders = vi.fn(async (_from: string, _to: string): Promise<readonly DailyOrder[]> => [])

vi.mock('../api', () => ({
  get api() {
    return {
      saveDailyOrder,
      setLastOrderDate,
      triggerGenerateJob,
      listDailyOrders,
    }
  },
}))

// 包一個 hook tester 把 context 裡的 setOrder 暴露到外部供 await 呼叫。
function CaptureSetOrder({ onReady }: { onReady: (so: (date: string, input: DailyOrderInput) => Promise<DailyOrder>) => void }) {
  const ctx = useDailyOrder()
  // setOrder 來自 useCallback，引用穩定；useEffect 只在 mount 跑一次就夠，
  // 不需要再 force re-render。onReady 由 renderProvider 同步指定，不會變。
  useEffect(() => {
    onReady(ctx.setOrder)
  }, [ctx.setOrder, onReady])
  return null
}

function Wrapper({ children }: { readonly children: ReactNode }) {
  return <DailyOrderProvider>{children}</DailyOrderProvider>
}

async function renderProvider(): Promise<{
  setOrder: (date: string, input: DailyOrderInput) => Promise<DailyOrder>
  root: Root
  container: HTMLDivElement
}> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  let setOrderRef: ((date: string, input: DailyOrderInput) => Promise<DailyOrder>) | null = null

  await act(async () => {
    root.render(
      <Wrapper>
        <CaptureSetOrder onReady={so => { setOrderRef = so }} />
      </Wrapper>,
    )
  })

  if (!setOrderRef) throw new Error('setOrder 尚未就緒（CaptureSetOrder useEffect 沒跑）')
  return { setOrder: setOrderRef, root, container }
}

// 把每個測試用過的 root 收起來，在 afterEach 統一 unmount + 清 DOM，
// 避免漏寫 unmount 造成 happy-dom 留節點污染下一個測試 + act warn。
const pendingRoots: Root[] = []

beforeEach(() => {
  saveDailyOrder.mockClear()
  setLastOrderDate.mockClear()
  triggerGenerateJob.mockClear()
  listDailyOrders.mockClear()
  // 預設行為：listDailyOrders 立刻回空陣列，saveDailyOrder 回原 order
  listDailyOrders.mockResolvedValue([])
  saveDailyOrder.mockImplementation(async (order: DailyOrder) => order)
  triggerGenerateJob.mockResolvedValue(undefined)
  setLastOrderDate.mockResolvedValue(undefined)
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

describe('DailyOrderProvider.setOrder 觸發鏈（T1）', () => {
  it('呼叫順序：saveDailyOrder → setLastOrderDate → triggerGenerateJob', async () => {
    const { setOrder, root } = await renderProvider()
    pendingRoots.push(root)

    const input: DailyOrderInput = {
      selectedTopics: ['tech'],
      specificRequest: 'AI',
      deliveryTime: '07:00',
      entryMode: 'topic',
      lengthTier: 'medium',
    }

    await act(async () => {
      await setOrder('2026-07-16', input)
    })

    // 必須依序發生：save 先拿到 DB 結果，setLastOrderDate 寫 localStorage，
    // triggerGenerateJob 才是 fire-and-forget 觸發 pipeline。
    expect(saveDailyOrder).toHaveBeenCalledTimes(1)
    expect(setLastOrderDate).toHaveBeenCalledTimes(1)
    expect(triggerGenerateJob).toHaveBeenCalledTimes(1)

    // 呼叫順序檢查：第 N 次呼叫的時間戳必須 ≤ 第 N+1 次。
    const saveOrder = saveDailyOrder.mock.invocationCallOrder[0]!
    const lastDateOrder = setLastOrderDate.mock.invocationCallOrder[0]!
    const triggerOrder = triggerGenerateJob.mock.invocationCallOrder[0]!
    expect(saveOrder).toBeLessThan(lastDateOrder)
    expect(lastDateOrder).toBeLessThan(triggerOrder)
  })

  it('triggerGenerateJob 收到的參數等於 setOrder 的 date', async () => {
    const { setOrder, root } = await renderProvider()
    pendingRoots.push(root)

    await act(async () => {
      await setOrder('2026-07-16', {
        selectedTopics: ['tech'],
        deliveryTime: '07:00',
      })
    })

    expect(triggerGenerateJob).toHaveBeenCalledWith('2026-07-16')
  })

  it('triggerGenerateJob reject 時 setOrder 仍 resolve（fire-and-forget 不打斷）', async () => {
    const failure = new Error('simulated network 500')
    triggerGenerateJob.mockRejectedValueOnce(failure)

    const { setOrder, root } = await renderProvider()
    pendingRoots.push(root)

    // 用微任務 + act 包起來，確保 fire-and-forget 的 promise 真的被吃下 catch。
    const captured = await act(async () => setOrder('2026-07-16', {
      selectedTopics: ['tech'],
      deliveryTime: '07:00',
    }))
    // 再讓 microtask 清乾淨（catch handler 會跑）
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    // setOrder 仍要回 saved order，不 throw、不變 undefined
    expect(captured).not.toBeNull()
    expect(captured.date).toBe('2026-07-16')
    expect(captured.selectedTopics).toEqual(['tech'])

    // console.warn 應該被呼叫過（失敗有跡可循）
    expect(console.warn).toHaveBeenCalled()
  })

  it('saveDailyOrder 失敗時 setOrder 仍要 reject（這個不是 fire-and-forget）', async () => {
    saveDailyOrder.mockRejectedValueOnce(new Error('PUT /daily-orders 500'))

    const { setOrder, root } = await renderProvider()
    pendingRoots.push(root)

    await act(async () => {
      await expect(
        setOrder('2026-07-16', {
          selectedTopics: ['tech'],
          deliveryTime: '07:00',
        }),
      ).rejects.toThrow('PUT /daily-orders 500')
    })

    // trigger 不應被呼叫（前面已經炸了）
    expect(triggerGenerateJob).not.toHaveBeenCalled()
  })
})