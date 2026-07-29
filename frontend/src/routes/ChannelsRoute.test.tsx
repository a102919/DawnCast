// @vitest-environment happy-dom
// ChannelsRoute（頻道探索頁）測試：渲染頻道網格、追蹤鈕反映訂閱狀態且點擊帶對的頻道、
// 空清單與錯誤狀態。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { ChannelsRoute } from './ChannelsRoute'
import type { ChannelPublic } from '../api'

const CHANNEL_TECH: ChannelPublic = {
  slug: 'tech-daily',
  name: '科技日報',
  description: '每天一則科技新知',
  topic: 'tech',
  episodeCount: 5,
}
const CHANNEL_BIZ: ChannelPublic = {
  slug: 'biz-weekly',
  name: '商業週報',
  description: null,
  topic: 'business',
  episodeCount: 2,
}

const listChannels = vi.fn(async (): Promise<readonly ChannelPublic[]> => [CHANNEL_TECH, CHANNEL_BIZ])
const toggle = vi.fn(async (_channel: ChannelPublic): Promise<void> => undefined)

vi.mock('../api', () => ({
  get api() {
    return { listChannels }
  },
  AppError: class AppError extends Error {},
}))

vi.mock('../state', () => ({
  // 只有 tech-daily 已追蹤，biz-weekly 沒有。
  useChannelSubscriptions: () => ({
    subscribed: new Map([['tech-daily', CHANNEL_TECH]]),
    toggle,
    has: (slug: string) => slug === 'tech-daily',
  }),
}))

async function renderRoute(): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/channels']}>
        <ChannelsRoute />
      </MemoryRouter>,
    )
  })
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

  return { root, container }
}

const pendingRoots: Root[] = []

beforeEach(() => {
  listChannels.mockClear()
  toggle.mockClear()
  listChannels.mockResolvedValue([CHANNEL_TECH, CHANNEL_BIZ])
})

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ChannelsRoute', () => {
  it('渲染全部頻道，追蹤鈕文字依 has() 顯示已追蹤/追蹤', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.textContent).toContain('科技日報')
    expect(container.textContent).toContain('商業週報')
    expect(container.textContent).toContain('5 集')
    expect(container.textContent).toContain('2 集')

    const buttons = Array.from(container.querySelectorAll('button'))
    const followedBtn = buttons.find(b => b.textContent === '已追蹤')
    const notFollowedBtn = buttons.find(b => b.textContent === '追蹤')
    expect(followedBtn).toBeDefined()
    expect(notFollowedBtn).toBeDefined()
  })

  it('點追蹤鈕呼叫 toggle 並帶上正確的頻道物件', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    const notFollowedBtn = Array.from(container.querySelectorAll('button')).find(
      b => b.textContent === '追蹤',
    )
    if (!notFollowedBtn) throw new Error('找不到「追蹤」按鈕')

    await act(async () => {
      notFollowedBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(toggle).toHaveBeenCalledWith(CHANNEL_BIZ)
  })

  it('沒有任何頻道時顯示空狀態', async () => {
    listChannels.mockResolvedValue([])
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.textContent).toContain('目前還沒有任何頻道')
  })

  it('載入失敗時顯示錯誤訊息', async () => {
    listChannels.mockRejectedValue(new Error('boom'))
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.textContent).toContain('頻道載入失敗')
  })
})
