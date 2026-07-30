// @vitest-environment happy-dom
// 首頁主題 chips 必須直接依 API 的 episode.topic 篩選，不另維護第二套分類狀態。
// Hero / Weekly 區塊於載入完成後必須依 mock 的 delivered / episodes 正確渲染。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HomeRoute } from './HomeRoute'
import type { Episode } from '../types/episode'
import type { MockEpisode } from '../lib'
import type { ChannelPublic, DailyOrder, RecommendedEpisode } from '../api'

const EPISODES: readonly MockEpisode[] = [
  { id: 'tech-1', title: 'AI Systems', titleZh: 'AI 系統', topic: 'tech', cefrLevel: 'B1', episode: 1, publishedAt: '2026-07-01' },
  { id: 'business-1', title: 'Market Signals', titleZh: '市場訊號', topic: 'business', cefrLevel: 'B1', episode: 2, publishedAt: '2026-07-02' },
  { id: 'culture-1', title: 'Street Art', titleZh: '街頭藝術', topic: 'culture', cefrLevel: 'B1', episode: 3, publishedAt: '2026-07-03' },
  { id: 'science-1', title: 'Quantum Light', titleZh: '量子光學', topic: 'science', cefrLevel: 'B1', episode: 4, publishedAt: '2026-07-04' },
]

const CHANNELS: readonly ChannelPublic[] = [
  { slug: 'tech-daily', name: '科技日報', topic: 'tech', episodeCount: 5 },
  { slug: 'biz-weekly', name: '商業週報', topic: 'business', episodeCount: 2 },
]

const listEpisodes = vi.fn(async (): Promise<readonly MockEpisode[]> => EPISODES)
const getEpisode = vi.fn(async (id: string): Promise<Episode> => ({
  id,
  title: EPISODES.find(episode => episode.id === id)?.title ?? id,
  audioUrl: null,
  segments: [],
  cues: [],
}))
// 預設無今日 delivery；個別測試會 override
const getDeliveredEpisode = vi.fn(async (): Promise<Episode | null> => null)
const toggleFavorite = vi.fn(async (): Promise<void> => undefined)
// 預設沒有任何推薦（未追蹤頻道時的常態）；RecommendedRail 據此決定要不要渲染。
const getRecommendedEpisodes = vi.fn(async (): Promise<readonly RecommendedEpisode[]> => [])
// 頻道區塊現在直接列出目錄（不再依訂閱狀態決定要不要顯示）。
const listChannels = vi.fn(async (): Promise<readonly ChannelPublic[]> => CHANNELS)

vi.mock('../api', () => ({
  get api() {
    return { listEpisodes, getEpisode, getDeliveredEpisode, getRecommendedEpisodes, listChannels }
  },
}))

// episodes 清單改由 useEpisodes（EpisodesProvider）提供；用可變 state 物件讓個別
// 測試能 override 清單內容（取代舊版 listEpisodes.mockResolvedValueOnce 的做法）。
const episodesState: { episodes: readonly MockEpisode[]; error: string | null } = {
  episodes: EPISODES,
  error: null,
}
const refreshEpisodes = vi.fn(async (): Promise<void> => undefined)

// activeOrder 預設 null（沒有進行中訂單 → 不觸發輪詢，避免 setTimeout 殘留
// 導致 act() warning）；個別測試可 override 成一筆進行中訂單來測 hero delivery。
const dailyOrderState: { activeOrder: DailyOrder | null } = { activeOrder: null }

vi.mock('../state', () => ({
  useActivity: () => ({ listenedEpisodeIds: new Set<string>() }),
  useVocab: () => ({ items: [] }),
  useFavorites: () => ({ favorites: new Set<string>(), toggle: toggleFavorite }),
  useEpisodes: () => ({
    episodes: episodesState.episodes,
    loading: false,
    error: episodesState.error,
    refresh: refreshEpisodes,
  }),
  useDailyOrder: () => ({
    activeOrder: dailyOrderState.activeOrder,
    history: [],
    createOrder: async () => ({}) as never,
    cancelOrder: async () => undefined,
    markPlayed: async () => null,
    loadMoreHistory: async () => undefined,
    refresh: async () => undefined,
  }),
}))

async function renderRoute(): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/']}>
        <HomeRoute />
      </MemoryRouter>,
    )
  })
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
    await new Promise(resolve => setTimeout(resolve, 250))
  })

  return { root, container }
}

function getEpisodeLibrary(container: HTMLDivElement): HTMLElement {
  const section = Array.from(container.querySelectorAll('section')).find(node =>
    node.textContent?.includes('選擇 podcast 開始學習'),
  )
  if (!section) throw new Error('找不到集數庫區塊')
  return section
}

async function selectTopic(container: HTMLDivElement, label: string): Promise<void> {
  const button = Array.from(container.querySelectorAll('button')).find(
    node => node.textContent === label,
  )
  if (!button) throw new Error(`找不到主題按鈕：${label}`)

  await act(async () => {
    button.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await new Promise(resolve => setTimeout(resolve, 250))
  })
}

const pendingRoots: Root[] = []

beforeEach(() => {
  listEpisodes.mockClear()
  getEpisode.mockClear()
  getDeliveredEpisode.mockClear()
  getDeliveredEpisode.mockResolvedValue(null)
  toggleFavorite.mockClear()
  refreshEpisodes.mockClear()
  episodesState.episodes = EPISODES
  episodesState.error = null
  getRecommendedEpisodes.mockClear()
  getRecommendedEpisodes.mockResolvedValue([])
  listChannels.mockClear()
  listChannels.mockResolvedValue(CHANNELS)
  dailyOrderState.activeOrder = null
  localStorage.clear()
})

afterEach(async () => {
  await act(async () => {
    for (const root of pendingRoots.splice(0)) root.unmount()
  })
  document.body.innerHTML = ''
  localStorage.clear()
})

describe('HomeRoute 主題篩選', () => {
  it.each([
    ['科技', 'AI Systems'],
    ['商業', 'Market Signals'],
    ['文化', 'Street Art'],
    ['科學', 'Quantum Light'],
  ] as const)('選擇「%s」只顯示對應集數', async (label, expectedTitle) => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    await selectTopic(container, label)

    const libraryText = getEpisodeLibrary(container).textContent ?? ''
    expect(libraryText).toContain(expectedTitle)
    for (const episode of EPISODES) {
      if (episode.title !== expectedTitle) expect(libraryText).not.toContain(episode.title)
    }
  })
})

describe('HomeRoute Hero 區塊', () => {
  it('無 delivery 時顯示 fallback 元件與 CTA', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    const fallback = container.querySelector('[data-testid="today-hero-fallback"]')
    expect(fallback).not.toBeNull()
    expect(container.querySelector('[data-testid="today-hero"]')).toBeNull()
    expect(fallback?.textContent).toContain('精選試聽')
  })

  it('有 delivery 時顯示 hero 元件並標題對應', async () => {
    dailyOrderState.activeOrder = {
      id: 'order-1',
      date: '2026-07-16',
      selectedTopics: [],
      status: 'queued',
      deliveryTime: '07:00',
      createdAt: '2026-07-16T00:00:00Z',
      updatedAt: '2026-07-16T00:00:00Z',
      entryMode: 'topic',
      lengthTier: 'medium',
      ready: true,
    }
    getDeliveredEpisode.mockResolvedValue({
      id: EPISODES[0]!.id,
      title: EPISODES[0]!.title,
      audioUrl: null,
      segments: [],
      cues: [],
    })
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    const hero = container.querySelector('[data-testid="today-hero"]')
    expect(hero).not.toBeNull()
    expect(container.querySelector('[data-testid="today-hero-fallback"]')).toBeNull()
    expect(hero?.textContent).toContain('AI Systems')
  })
})

describe('HomeRoute 今日推薦', () => {
  it('episodes 載入後顯示今日推薦列，最多 2 張', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    const row = container.querySelector('[data-testid="weekly-row"]')
    expect(row).not.toBeNull()
    const cards = container.querySelectorAll('[data-testid="weekly-card"]')
    expect(cards.length).toBe(2)
    expect(row?.textContent).toContain('今日推薦')
  })

  it('episodes 為空時整個 weekly 區塊不渲染', async () => {
    episodesState.episodes = []
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.querySelector('[data-testid="weekly-row"]')).toBeNull()
  })
})

describe('HomeRoute 頻道區塊', () => {
  it('頻道目錄為空時顯示空狀態提示', async () => {
    listChannels.mockResolvedValueOnce([])
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.textContent).toContain('目前還沒有任何頻道')
  })

  it('直接列出頻道目錄，每張卡連到對應頻道詳情頁；4 個以內不顯示「全部」', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.textContent).toContain('科技日報')
    expect(container.textContent).toContain('商業週報')
    expect(container.querySelector('a[href="/channels/tech-daily"]')).not.toBeNull()
    expect(container.querySelector('a[href="/channels/biz-weekly"]')).not.toBeNull()
    expect(container.querySelector('a[href="/channels"]')).toBeNull()
  })

  it('超過 4 個頻道時只顯示前 4 個，並顯示「全部」連到 /channels', async () => {
    listChannels.mockResolvedValueOnce([
      ...CHANNELS,
      { slug: 'culture-cast', name: '文化廣播', topic: 'culture', episodeCount: 1 },
      { slug: 'science-hour', name: '科學時間', topic: 'science', episodeCount: 4 },
      { slug: 'extra-channel', name: '額外頻道', topic: 'tech', episodeCount: 1 },
    ])

    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.querySelector('a[href="/channels/culture-cast"]')).not.toBeNull()
    expect(container.querySelector('a[href="/channels/science-hour"]')).not.toBeNull()
    expect(container.querySelector('a[href="/channels/extra-channel"]')).toBeNull()
    const viewAll = container.querySelector('a[href="/channels"]')
    expect(viewAll).not.toBeNull()
    expect(viewAll?.textContent).toContain('全部')
  })

  it('getRecommendedEpisodes 回空清單時，推薦區塊不渲染', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.textContent).not.toContain('根據你追蹤的頻道')
  })

  it('getRecommendedEpisodes 有資料時渲染推薦區塊，卡片顯示頻道名稱', async () => {
    const recommended: RecommendedEpisode = {
      id: 'rec-1',
      title: 'Recommended Ep',
      titleZh: '推薦集數',
      topic: 'tech',
      cefrLevel: 'B1',
      episode: 9,
      publishedAt: '2026-07-20',
      channelSlug: 'tech-daily',
      channelName: '科技日報',
    }
    getRecommendedEpisodes.mockResolvedValueOnce([recommended])

    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(container.textContent).toContain('根據你追蹤的頻道')
    expect(container.textContent).toContain('Recommended Ep')
    expect(container.textContent).toContain('科技日報')
  })

  it('每日訂閱入口卡存在且連到 /daily（從底部導覽移出後的補償入口）', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    // 無 delivery 時 HomeHeroFallback 本身也有一條「立即點餐」CTA 連到 /daily，
    // 所以要在全部 /daily 連結裡找「每日訂閱」這張補償入口卡，不能只抓第一個。
    const dailyLinks = Array.from(container.querySelectorAll('a[href="/daily"]'))
    expect(dailyLinks.some(link => link.textContent?.includes('每日訂閱'))).toBe(true)
  })
})
