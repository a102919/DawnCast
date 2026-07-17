// @vitest-environment happy-dom
// ProgressRoute 測試（回歸鎖：「已聽集數」要直接顯示 listenedIds.size，不是拿
// listenEps.filter(假資料).length——後者只要 listened id 剛好不在 listEpisodes()
// 回傳的集數清單裡就會被漏算，跟首頁等處顯示的 listenedIds.size 對不上）。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ProgressRoute } from './ProgressRoute'
import type { MockEpisode } from './episodeData'

// listEpisodes() 回傳的集數 id 跟下面 listenedIds 刻意完全不重疊：
// 如果程式碼退回用「episodes.filter(ep => listenedIds.has(ep.id)).length」計算，
// 這裡永遠算出 0，跟 listenedIds.size（3）對不上，測試就會抓到。
const LIST_EPISODES: readonly MockEpisode[] = [
  { id: 'ep-a', title: 'Ep A', titleZh: '甲集', topic: 'tech', cefrLevel: 'B1', episode: 1, publishedAt: '2026-07-01' },
  { id: 'ep-b', title: 'Ep B', titleZh: '乙集', topic: 'business', cefrLevel: 'B1', episode: 2, publishedAt: '2026-07-02' },
]

const NOT_IN_LIST_IDS = ['zz-not-in-list', 'yy-not-in-list', 'xx-not-in-list']

const listEpisodes = vi.fn(async (): Promise<readonly MockEpisode[]> => LIST_EPISODES)

vi.mock('../api', () => ({
  get api() {
    return { listEpisodes }
  },
}))

vi.mock('../state', () => ({
  useVocab: () => ({
    items: [],
    isLoading: false,
    addVocab: vi.fn(),
    removeVocab: vi.fn(),
    clearVocab: vi.fn(),
    isInVocab: () => false,
    updateCardReview: vi.fn(),
  }),
  useListened: () => ({
    listenedIds: new Set(NOT_IN_LIST_IDS),
    markAsListened: vi.fn(),
  }),
  useActivity: () => ({
    streakDates: [],
    listenMinutes: {},
    lookupCount: {},
    listenedEpisodeIds: new Set(NOT_IN_LIST_IDS),
    lastPlayedEpisodeId: null,
    lastPlayedPosition: null,
    markListened: vi.fn(),
    addListenMinutes: vi.fn(),
    addLookupCount: vi.fn(),
    setLastPlayed: vi.fn(),
  }),
}))

async function renderRoute(): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(<ProgressRoute />)
  })
  // listEpisodes() 的 await 鏈跑完，讓 setEpisodes 的 re-render 在 act 內結算。
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

  return { root, container }
}

function readStatValue(container: HTMLDivElement, label: string): string | null {
  const card = Array.from(container.querySelectorAll('.grid > div')).find(
    el => el.textContent?.includes(label),
  )
  const valueEl = card?.querySelector('.text-2xl')
  return valueEl?.firstChild?.textContent?.trim() ?? null
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

describe('ProgressRoute：已聽集數直接顯示 listenedIds.size', () => {
  it('listened id 完全不在 listEpisodes() 清單裡時，已聽集數仍顯示 listenedIds.size（3），不是 filter 後的 0', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(listEpisodes).toHaveBeenCalledTimes(1)
    expect(readStatValue(container, '已聽集數')).toBe('3')
  })
})
