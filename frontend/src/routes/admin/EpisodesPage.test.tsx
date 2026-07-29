// @vitest-environment happy-dom
// EpisodesPage：單集數據總覽的排序與展開行為。
//
// 「播放次數只從部署後起算」在這裡體現為兩集數字互不相同（5 vs 20），排序切換
// 必須真的改變順序，不能巧合通過；沒有 stages 的列不該能展開。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { EpisodesPage } from './EpisodesPage'
import type { AdminEpisodeStats, AdminEpisodeStatsResponse } from '../../api'

const { getAdminEpisodeStats, MockAppError } = vi.hoisted(() => {
  const items: readonly AdminEpisodeStats[] = [
    {
      id: 'ep-quiet', title: '播放少但聽完人數多', topic: 'tech', cefrLevel: 'B1',
      isFree: true, episodeNo: 1, publishedAt: '2026-07-01', createdAt: '2026-07-01T00:00:00Z',
      channelName: 'AI 頻道', hasAudio: true,
      playCount: 5, listenerCount: 10, favoriteCount: 1,
      inputTokens: 100, outputTokens: 50, wallMs: 5000,
      stages: [{ node: 'write_script', durationMs: 2000, status: 'ok', attempt: 1 }],
    },
    {
      id: 'ep-viral', title: '播放多但聽完人數少', topic: 'business', cefrLevel: 'A2',
      isFree: false, episodeNo: 2, publishedAt: '2026-07-02', createdAt: '2026-06-30T00:00:00Z',
      channelName: null, hasAudio: false,
      playCount: 20, listenerCount: 2, favoriteCount: 9,
      inputTokens: 900, outputTokens: 100, wallMs: null,
      stages: [],
    },
  ]
  const response: AdminEpisodeStatsResponse = {
    episodeCount: 2, totalInputTokens: 1000, totalOutputTokens: 150, totalPlayCount: 25, items,
  }
  return {
    getAdminEpisodeStats: vi.fn(async (): Promise<AdminEpisodeStatsResponse> => response),
    MockAppError: class MockAppError extends Error {},
  }
})

vi.mock('../../api', () => ({
  get api() {
    return { getAdminEpisodeStats }
  },
  AppError: MockAppError,
}))

function findChip(container: HTMLElement, text: string): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll('button')).find(b => b.textContent?.trim() === text)
  if (!btn) throw new Error(`找不到按鈕：${text}`)
  return btn
}

function rowTitles(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('p.text-sm.text-text-primary.truncate')).map(
    p => p.textContent ?? '',
  )
}

async function renderPage(): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter>
        <EpisodesPage />
      </MemoryRouter>,
    )
  })
  await act(async () => {
    await Promise.resolve()
  })

  return { root, container }
}

const pendingRoots: Root[] = []

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('EpisodesPage', () => {
  it('渲染總覽數字與每列的播放／聽完／收藏', async () => {
    const { root, container } = await renderPage()
    pendingRoots.push(root)

    expect(getAdminEpisodeStats).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('播放少但聽完人數多')
    expect(container.textContent).toContain('AI 頻道')
    expect(container.textContent).toContain('EP 1')
    expect(container.textContent).toContain('公開')
    expect(container.textContent).toContain('私有')
  })

  it('預設依最新排序（後端 createdAt desc），切「播放最多」後順序改變', async () => {
    const { root, container } = await renderPage()
    pendingRoots.push(root)

    expect(rowTitles(container)).toEqual(['播放少但聽完人數多', '播放多但聽完人數少'])

    await act(async () => {
      findChip(container, '播放最多').click()
    })
    expect(rowTitles(container)).toEqual(['播放多但聽完人數少', '播放少但聽完人數多'])

    await act(async () => {
      findChip(container, '聽完最多').click()
    })
    expect(rowTitles(container)).toEqual(['播放少但聽完人數多', '播放多但聽完人數少'])
  })

  it('展開有 stages 的列會顯示分階段耗時；沒有 stages 的列點了沒反應', async () => {
    const { root, container } = await renderPage()
    pendingRoots.push(root)

    const buttons = Array.from(container.querySelectorAll('button')).filter(b =>
      b.textContent?.includes('播放少但聽完人數多') || b.textContent?.includes('播放多但聽完人數少'),
    )
    const [withStages, withoutStages] = buttons
    expect(withoutStages?.hasAttribute('disabled')).toBe(true)

    await act(async () => {
      withoutStages?.click()
    })
    expect(container.textContent).not.toContain('write_script')

    await act(async () => {
      withStages?.click()
    })
    expect(container.textContent).toContain('write_script')
  })
})
