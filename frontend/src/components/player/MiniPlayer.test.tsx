// @vitest-environment happy-dom
// MiniPlayer：離開播放頁後背景續播的迷你播放列。
//
// 覆蓋：
//  - 沒有 currentEpisode 時不渲染。
//  - 在 /player、/login 上即使有 currentEpisode 也不渲染（避免跟全螢幕播放頁重複）。
//  - 其他頁面顯示標題，點播放/暫停鈕只切換播放狀態，不觸發導頁。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MiniPlayer } from './MiniPlayer'
import type { Episode } from '../../types/episode'

const play = vi.fn()
const pause = vi.fn()
let mockEpisode: Episode | null = null
let mockIsPlaying = false

vi.mock('../../state', () => ({
  usePlayer: () => ({
    currentEpisode: mockEpisode,
    isPlaying: mockIsPlaying,
    currentTime: 30,
    duration: 60,
    play,
    pause,
  }),
}))

const EPISODE: Episode = {
  id: 'ep-1',
  title: '測試集數標題',
  audioUrl: null,
  segments: [],
  cues: [],
}

function renderAt(path: string): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => {
    root.render(
      <MemoryRouter initialEntries={[path]}>
        <MiniPlayer />
      </MemoryRouter> as ReactNode,
    )
  })
  return { root, container }
}

const pendingRoots: Root[] = []

afterEach(() => {
  for (const r of pendingRoots.splice(0)) r.unmount()
  document.body.innerHTML = ''
  mockEpisode = null
  mockIsPlaying = false
  play.mockClear()
  pause.mockClear()
})

describe('MiniPlayer：顯示條件', () => {
  it('沒有 currentEpisode 時不渲染任何節點', () => {
    mockEpisode = null
    const { root, container } = renderAt('/')
    pendingRoots.push(root)
    expect(container.textContent).toBe('')
  })

  it('在 /player 上即使有 episode 也不渲染', () => {
    mockEpisode = EPISODE
    const { root, container } = renderAt('/player/ep-1')
    pendingRoots.push(root)
    expect(container.textContent).toBe('')
  })

  it('在 /login 上不渲染', () => {
    mockEpisode = EPISODE
    const { root, container } = renderAt('/login')
    pendingRoots.push(root)
    expect(container.textContent).toBe('')
  })

  it('其他頁面顯示集數標題', () => {
    mockEpisode = EPISODE
    const { root, container } = renderAt('/vocab')
    pendingRoots.push(root)
    expect(container.textContent).toContain('測試集數標題')
  })
})

describe('MiniPlayer：播放控制', () => {
  it('點播放/暫停鈕只切換播放狀態，不會拋錯或導頁', () => {
    mockEpisode = EPISODE
    mockIsPlaying = false
    const { root, container } = renderAt('/vocab')
    pendingRoots.push(root)
    const button = container.querySelector('button')
    expect(button).not.toBeNull()
    act(() => { button?.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(play).toHaveBeenCalledTimes(1)
    expect(pause).not.toHaveBeenCalled()
  })
})
