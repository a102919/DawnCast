// @vitest-environment happy-dom
// ChannelDetailRoute（頻道詳情頁）測試：頻道資訊 + 集數列表渲染、追蹤鈕反映訂閱狀態、
// 404/錯誤狀態、空集數狀態。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { ChannelDetailRoute } from './ChannelDetailRoute'
import type { ChannelPublic } from '../api'
import type { MockEpisode } from '../lib'

const CHANNEL_TECH: ChannelPublic = {
  slug: 'tech-daily',
  name: '科技日報',
  description: '每天一則科技新知',
  topic: 'tech',
  episodeCount: 2,
}

const EPISODES: readonly MockEpisode[] = [
  { id: 'ep-1', title: 'Episode One', titleZh: '第一集', topic: 'tech', cefrLevel: 'B1', episode: 1, publishedAt: '2026-07-01' },
  { id: 'ep-2', title: 'Episode Two', titleZh: '第二集', topic: 'tech', cefrLevel: 'B2', episode: 2, publishedAt: '2026-07-08' },
]

const getChannel = vi.fn(async (_slug: string): Promise<ChannelPublic> => CHANNEL_TECH)
const listEpisodes = vi.fn(async (_opts?: { readonly channel?: string }): Promise<readonly MockEpisode[]> => EPISODES)
const toggle = vi.fn(async (_channel: ChannelPublic): Promise<void> => undefined)

vi.mock('../api', () => ({
  get api() {
    return { getChannel, listEpisodes }
  },
  AppError: class AppError extends Error {},
}))

vi.mock('../state', () => ({
  useChannelSubscriptions: () => ({
    subscribed: new Map(),
    toggle,
    has: (slug: string) => slug === 'tech-daily',
  }),
  // EpisodeRow（集數列表項）內部用到，這裡跟 HomeRoute.test.tsx 一樣給靜態假值。
  useActivity: () => ({ listenedEpisodeIds: new Set<string>() }),
  useFavorites: () => ({ favorites: new Set<string>(), toggle: vi.fn() }),
}))

async function renderRoute(path: string): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/channels/:slug" element={<ChannelDetailRoute />} />
        </Routes>
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
  getChannel.mockClear()
  listEpisodes.mockClear()
  toggle.mockClear()
  getChannel.mockResolvedValue(CHANNEL_TECH)
  listEpisodes.mockResolvedValue(EPISODES)
})

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('ChannelDetailRoute', () => {
  it('渲染頻道資訊與該頻道的集數列表，已追蹤顯示「已追蹤」', async () => {
    const { root, container } = await renderRoute('/channels/tech-daily')
    pendingRoots.push(root)

    expect(listEpisodes).toHaveBeenCalledWith({ channel: 'tech-daily' })
    expect(container.textContent).toContain('科技日報')
    expect(container.textContent).toContain('每天一則科技新知')
    expect(container.textContent).toContain('2 集')
    expect(container.textContent).toContain('Episode One')
    expect(container.textContent).toContain('Episode Two')
    expect(container.textContent).toContain('已追蹤')
  })

  it('點追蹤鈕呼叫 toggle 並帶上正確的頻道物件', async () => {
    const { root, container } = await renderRoute('/channels/tech-daily')
    pendingRoots.push(root)

    const followBtn = Array.from(container.querySelectorAll('button')).find(
      b => b.textContent === '已追蹤',
    )
    if (!followBtn) throw new Error('找不到追蹤按鈕')

    await act(async () => {
      followBtn.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    })

    expect(toggle).toHaveBeenCalledWith(CHANNEL_TECH)
  })

  it('頻道不存在時顯示錯誤訊息', async () => {
    getChannel.mockRejectedValue(new Error('not found'))
    const { root, container } = await renderRoute('/channels/no-such-channel')
    pendingRoots.push(root)

    expect(container.textContent).toContain('找不到這個頻道')
  })

  it('頻道沒有集數時顯示空狀態', async () => {
    listEpisodes.mockResolvedValue([])
    const { root, container } = await renderRoute('/channels/tech-daily')
    pendingRoots.push(root)

    expect(container.textContent).toContain('這個頻道還沒有集數')
  })
})
