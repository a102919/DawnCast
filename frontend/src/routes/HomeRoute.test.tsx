// @vitest-environment happy-dom
// 首頁主題 chips 必須直接依 API 的 episode.topic 篩選，不另維護第二套分類狀態。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HomeRoute } from './HomeRoute'
import type { Episode } from '../types/episode'
import type { MockEpisode } from '../lib'

const EPISODES: readonly MockEpisode[] = [
  { id: 'tech-1', title: 'AI Systems', titleZh: 'AI 系統', topic: 'tech', cefrLevel: 'B1', episode: 1, publishedAt: '2026-07-01' },
  { id: 'business-1', title: 'Market Signals', titleZh: '市場訊號', topic: 'business', cefrLevel: 'B1', episode: 2, publishedAt: '2026-07-02' },
  { id: 'culture-1', title: 'Street Art', titleZh: '街頭藝術', topic: 'culture', cefrLevel: 'B1', episode: 3, publishedAt: '2026-07-03' },
  { id: 'science-1', title: 'Quantum Light', titleZh: '量子光學', topic: 'science', cefrLevel: 'B1', episode: 4, publishedAt: '2026-07-04' },
]

const listEpisodes = vi.fn(async (): Promise<readonly MockEpisode[]> => EPISODES)
const getEpisode = vi.fn(async (id: string): Promise<Episode> => ({
  id,
  title: EPISODES.find(episode => episode.id === id)?.title ?? id,
  audioUrl: `https://example.com/${id}.mp3`,
  cues: [],
}))
const toggleFavorite = vi.fn(async (): Promise<void> => undefined)

vi.mock('../api', () => ({
  get api() {
    return { listEpisodes, getEpisode }
  },
}))

vi.mock('../state', () => ({
  useSettings: () => ({ settings: { preferredTopics: ['business'] } }),
  useActivity: () => ({ listenedEpisodeIds: new Set<string>() }),
  useVocab: () => ({ items: [] }),
  useFavorites: () => ({ favorites: new Set<string>(), toggle: toggleFavorite }),
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
    node.textContent?.includes('所有集數'),
  )
  if (!section) throw new Error('找不到所有集數區塊')
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
  toggleFavorite.mockClear()
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

describe('HomeRoute 主題偏好推薦', () => {
  it('偏好主題會決定今日推薦，但不改變集數庫順序', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    const heroSection = Array.from(container.querySelectorAll('section')).find(node =>
      node.textContent?.includes('今日推薦'),
    )
    if (!heroSection) throw new Error('找不到今日推薦區塊')

    expect(heroSection.textContent).toContain('Market Signals')
    expect(getEpisode).toHaveBeenCalledWith('business-1')
    expect(getEpisode).not.toHaveBeenCalledWith('tech-1')
  })
})
