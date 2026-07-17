// @vitest-environment happy-dom
// FavoritesRoute 測試（回歸鎖：收藏頁要用 api.listEpisodes() 的真資料去 filter，
// 不是拿 episodeData.ts 寫死的假 EPISODES 陣列——假資料的 id 跟真實 favorites id
// 對不上，filter 完永遠是空陣列）。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { FavoritesRoute } from './FavoritesRoute'
import type { MockEpisode } from './episodeData'

// 3 筆真實資料，其中 2 筆的 id 出現在收藏清單裡、1 筆沒有。
const REAL_EPISODES: readonly MockEpisode[] = [
  { id: 'real-1', title: 'Real One', titleZh: '真實第一集', topic: 'tech', cefrLevel: 'B1', episode: 1, publishedAt: '2026-07-01' },
  { id: 'real-2', title: 'Real Two', titleZh: '真實第二集', topic: 'business', cefrLevel: 'B1', episode: 2, publishedAt: '2026-07-02' },
  { id: 'real-3', title: 'Real Three', titleZh: '真實第三集', topic: 'science', cefrLevel: 'A2', episode: 3, publishedAt: '2026-07-03' },
]

const listEpisodes = vi.fn(async (): Promise<readonly MockEpisode[]> => REAL_EPISODES)

vi.mock('../api', () => ({
  get api() {
    return { listEpisodes }
  },
}))

// useFavorites 直接換成靜態假值：favorites 裡放 2 個「真實」id（real-1、real-3），
// 對照修之前用假 EPISODES（episodeData.ts 的 loop_engineering 之類的 id）filter，
// 這 2 個 id 一定 filter 不出東西、清單永遠空。
vi.mock('../state', () => ({
  useFavorites: () => ({
    favorites: new Set(['real-1', 'real-3']),
    toggle: vi.fn(),
    has: (id: string) => id === 'real-1' || id === 'real-3',
  }),
}))

async function renderRoute(): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/favorites']}>
        <FavoritesRoute />
      </MemoryRouter>,
    )
  })
  // listEpisodes() 的 await 鏈跑完，讓 setEpisodes 的 re-render 在 act 內結算。
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

  return { root, container }
}

const pendingRoots: Root[] = []

beforeEach(() => {
  listEpisodes.mockClear()
})

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('FavoritesRoute：用真實 api.listEpisodes() 資料 filter 收藏', () => {
  it('顯示 favorites 裡的 2 筆真實集數，不是空清單', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(listEpisodes).toHaveBeenCalledTimes(1)

    // 空狀態文案不該出現（修之前用假資料 filter，一定會落到這個空狀態）。
    expect(container.textContent).not.toContain('收藏清單是空的')

    // 標題文字：real-1 / real-3 要出現，real-2（沒被收藏）不該出現。
    expect(container.textContent).toContain('Real One')
    expect(container.textContent).toContain('Real Three')
    expect(container.textContent).not.toContain('Real Two')

    // 收藏頁連到 /player/{id} 的連結數量要等於 favorites 命中的筆數（2 筆）。
    const links = container.querySelectorAll('a[href^="/player/"]')
    expect(links).toHaveLength(2)
    const hrefs = Array.from(links).map(a => a.getAttribute('href'))
    expect(hrefs).toEqual(expect.arrayContaining(['/player/real-1', '/player/real-3']))

    // 標題列的統計數字也要對上。
    expect(container.textContent).toContain('共 2 集 podcast')
  })
})
