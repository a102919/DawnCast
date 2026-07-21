// @vitest-environment happy-dom
// PlayerRoute 測試（回歸鎖：/player/:id 要用 URL 的 id 呼叫 api.getEpisode，
// 不是不管網址是什麼都固定播 listEpisodes()[0]）。

// React 19 對 act() 的環境感知旗標，沒設會跳 warn；不影響測試通過但很吵。
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
//
// 修之前：loadEpisode 不管 useParams 給了什麼 id，一律 listEpisodes() 拿全部集數
// 再取 [0] 呼叫 getEpisode，導致 /player/ep-2 這種深連結永遠播到第一集。
// 修之後：有 id 就直接 api.getEpisode(id)，list[0] fallback 只在無 id（/player）時才用。
//
// 不裝 @testing-library/react，直接用 react-dom/client.createRoot + happy-dom
// 提供的 window/document，跟 DailyOrderProvider.test.tsx 同一套風格。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { PlayerRoute } from './PlayerRoute'
import type { Episode } from '../types/episode'
import type { MockEpisode } from '../lib'

const MOCK_LIST: readonly MockEpisode[] = [
  { id: 'ep-1', title: 'Ep One', titleZh: '第一集', topic: 'tech', cefrLevel: 'B1', episode: 1, publishedAt: '2026-07-01' },
  { id: 'ep-2', title: 'Ep Two', titleZh: '第二集', topic: 'tech', cefrLevel: 'B1', episode: 2, publishedAt: '2026-07-02' },
]

function mockEpisodeFor(id: string): Episode {
  return { id, title: `Episode ${id}`, audioUrl: `https://example.com/${id}.mp3`, cues: [] }
}

// Mock api 模組：spyOn 真物件太繞，直接替換整個 export（跟 DailyOrderProvider.test.tsx 同手法）。
const listEpisodes = vi.fn(async (): Promise<readonly MockEpisode[]> => MOCK_LIST)
const getEpisode = vi.fn(async (id: string): Promise<Episode> => mockEpisodeFor(id))
const getDeliveredEpisode = vi.fn(async (_date: string): Promise<Episode | null> => null)

vi.mock('../api', () => ({
  get api() {
    return { listEpisodes, getEpisode, getDeliveredEpisode }
  },
}))

// PlayerRoute 直接呼叫的 state hooks 全部換成靜態假值：這個測試只關心「URL 的
// id 有沒有正確傳進 api.getEpisode」，不需要真的掛整棵 Provider tree。
vi.mock('../state', () => ({
  usePlayer: () => ({
    currentTime: 0,
    isPlaying: false,
    duration: 0,
    playbackRate: 1,
    videoRef: { current: null },
    seekTo: vi.fn(),
    setVideoRef: vi.fn(),
    play: vi.fn(),
    pause: vi.fn(),
    setPlaybackRate: vi.fn(),
    loadProgress: () => ({ currentTime: 0, exists: false }),
  }),
  useSettings: () => ({
    settings: {
      popupEnabled: true,
      popupDontShowAgain: false,
      playbackRate: 1,
      fontSize: 'md',
      theme: 'auto',
      preferredTopics: [],
      defaultDeliveryTime: '07:00',
    },
    updateSettings: vi.fn(),
    resetPopupPreferences: vi.fn(),
  }),
  useDailyOrder: () => ({
    todayDate: '2026-07-17',
    orders: new Map(),
    getOrder: () => null,
    setOrder: vi.fn(),
    deleteOrder: vi.fn(),
    markPlayed: vi.fn(),
  }),
  useActivity: () => ({
    streakDates: [],
    listenMinutes: {},
    lookupCount: {},
    listenedEpisodeIds: new Set<string>(),
    lastPlayedEpisodeId: null,
    lastPlayedPosition: null,
    markListened: vi.fn(),
    addListenMinutes: vi.fn(),
    addLookupCount: vi.fn(),
    setLastPlayed: vi.fn(),
  }),
  useVocab: () => ({
    items: [],
    isLoading: false,
    addVocab: vi.fn(),
    removeVocab: vi.fn(),
    clearVocab: vi.fn(),
    isInVocab: () => false,
    updateCardReview: vi.fn(),
  }),
}))

function Wrapper({ initialPath, children }: { readonly initialPath: string; readonly children: ReactNode }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/player" element={children} />
        <Route path="/player/:id" element={children} />
      </Routes>
    </MemoryRouter>
  )
}

async function renderAt(initialPath: string): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(<Wrapper initialPath={initialPath}><PlayerRoute /></Wrapper>)
  })
  // loadEpisode 的 await 鏈跑完、讓 setEpisode 產生的 re-render 也在 act 內結算。
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })

  return { root, container }
}

const pendingRoots: Root[] = []

beforeEach(() => {
  listEpisodes.mockClear()
  getEpisode.mockClear()
  getDeliveredEpisode.mockClear()
})

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('PlayerRoute：/player/:id 要播 URL 指定的集數', () => {
  it('id=ep-2 時 api.getEpisode 收到的參數是 ep-2，不是 list 裡第一筆 ep-1', async () => {
    const { root } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    expect(getEpisode).toHaveBeenCalledTimes(1)
    expect(getEpisode).toHaveBeenCalledWith('ep-2')
    // 修之前的邏輯會先呼叫 listEpisodes() 再用 list[0].id 呼叫 getEpisode；
    // 有 id 時完全不該碰 listEpisodes。
    expect(listEpisodes).not.toHaveBeenCalled()
  })

  it('無 id（/player）時才 fallback 到 listEpisodes()[0]', async () => {
    const { root } = await renderAt('/player')
    pendingRoots.push(root)

    expect(listEpisodes).toHaveBeenCalledTimes(1)
    expect(getEpisode).toHaveBeenCalledWith('ep-1')
  })
})
