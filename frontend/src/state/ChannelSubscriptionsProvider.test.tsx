// @vitest-environment happy-dom
// ChannelSubscriptionsProvider 測試：初始載入 + toggle 樂觀更新（鏡射 DailyOrderProvider.test.tsx
// 的 createRoot + act + Capture-context 寫法，不裝 @testing-library/react）。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, useEffect, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ChannelSubscriptionsProvider } from './ChannelSubscriptionsProvider'
import { useChannelSubscriptions } from './useChannelSubscriptions'
import type { ChannelSubscriptionsContextValue } from './channelSubscriptionsContextValue'
import type { ChannelPublic } from '../api'

const CHANNEL_A: ChannelPublic = { slug: 'tech-daily', name: '科技日報', topic: 'tech', episodeCount: 3 }
const CHANNEL_B: ChannelPublic = { slug: 'biz-weekly', name: '商業週報', topic: 'business', episodeCount: 2 }

const listMySubscriptions = vi.fn(async (): Promise<readonly ChannelPublic[]> => [CHANNEL_A])
const subscribeChannel = vi.fn(async (_slug: string): Promise<void> => undefined)
const unsubscribeChannel = vi.fn(async (_slug: string): Promise<void> => undefined)

vi.mock('../api', () => ({
  get api() {
    return { listMySubscriptions, subscribeChannel, unsubscribeChannel }
  },
}))

// 沒有依賴陣列：每次 render 都重新捕捉，讓 getCtx() 永遠讀得到最新的 subscribed/has。
function CaptureContext({ onReady }: { onReady: (ctx: ChannelSubscriptionsContextValue) => void }) {
  const ctx = useChannelSubscriptions()
  useEffect(() => {
    onReady(ctx)
  })
  return null
}

function Wrapper({ children }: { readonly children: ReactNode }) {
  return <ChannelSubscriptionsProvider>{children}</ChannelSubscriptionsProvider>
}

async function renderProvider(): Promise<{ getCtx: () => ChannelSubscriptionsContextValue; root: Root }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  let latest: ChannelSubscriptionsContextValue | null = null

  await act(async () => {
    root.render(
      <Wrapper>
        <CaptureContext onReady={ctx => { latest = ctx }} />
      </Wrapper>,
    )
  })
  // 讓初次載入的 listMySubscriptions() promise 落地
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

  if (!latest) throw new Error('context 尚未就緒（CaptureContext 還沒跑）')
  return {
    getCtx: () => {
      if (!latest) throw new Error('context 尚未就緒')
      return latest
    },
    root,
  }
}

const pendingRoots: Root[] = []

beforeEach(() => {
  listMySubscriptions.mockClear()
  subscribeChannel.mockClear()
  unsubscribeChannel.mockClear()
  listMySubscriptions.mockResolvedValue([CHANNEL_A])
  subscribeChannel.mockResolvedValue(undefined)
  unsubscribeChannel.mockResolvedValue(undefined)
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ChannelSubscriptionsProvider', () => {
  it('初始載入 listMySubscriptions 的結果', async () => {
    const { getCtx, root } = await renderProvider()
    pendingRoots.push(root)

    expect(getCtx().has('tech-daily')).toBe(true)
    expect(getCtx().subscribed.get('tech-daily')).toEqual(CHANNEL_A)
    expect(getCtx().has('biz-weekly')).toBe(false)
  })

  it('toggle 未追蹤的頻道：樂觀加入 + 呼叫 subscribeChannel（不是 unsubscribe）', async () => {
    const { getCtx, root } = await renderProvider()
    pendingRoots.push(root)

    await act(async () => {
      await getCtx().toggle(CHANNEL_B)
    })

    expect(getCtx().has('biz-weekly')).toBe(true)
    expect(subscribeChannel).toHaveBeenCalledWith('biz-weekly')
    expect(unsubscribeChannel).not.toHaveBeenCalled()
  })

  it('toggle 已追蹤的頻道：樂觀移除 + 呼叫 unsubscribeChannel', async () => {
    const { getCtx, root } = await renderProvider()
    pendingRoots.push(root)

    await act(async () => {
      await getCtx().toggle(CHANNEL_A)
    })

    expect(getCtx().has('tech-daily')).toBe(false)
    expect(unsubscribeChannel).toHaveBeenCalledWith('tech-daily')
    expect(subscribeChannel).not.toHaveBeenCalled()
  })

  it('subscribeChannel 失敗時 toggle 不 throw，樂觀狀態不回滾（比照 FavoritesProvider 行為)', async () => {
    subscribeChannel.mockRejectedValueOnce(new Error('network error'))
    const { getCtx, root } = await renderProvider()
    pendingRoots.push(root)

    await act(async () => {
      await getCtx().toggle(CHANNEL_B)
    })

    expect(getCtx().has('biz-weekly')).toBe(true)
    expect(console.warn).toHaveBeenCalled()
  })

  it('初始載入失敗時不 throw，subscribed 維持空 Map', async () => {
    listMySubscriptions.mockRejectedValueOnce(new Error('network error'))
    const { getCtx, root } = await renderProvider()
    pendingRoots.push(root)

    expect(getCtx().subscribed.size).toBe(0)
    expect(console.warn).toHaveBeenCalled()
  })
})
