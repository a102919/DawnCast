// @vitest-environment happy-dom
// EpisodesPage：單集數據總覽的排序，與點列開啟生成過程 dialog 的行為。
//
// 「播放次數只從部署後起算」在這裡體現為兩集數字互不相同（5 vs 20），排序切換
// 必須真的改變順序，不能巧合通過。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { EpisodesPage } from './EpisodesPage'
import type { AdminEpisodeGeneration, AdminEpisodeStats, AdminEpisodeStatsResponse } from '../../api'

const { getAdminEpisodeStats, getAdminEpisodeGeneration, MockAppError } = vi.hoisted(() => {
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
  const generation: AdminEpisodeGeneration = {
    status: 'succeeded',
    enqueuedAt: '2026-07-01T00:00:00Z', startedAt: '2026-07-01T00:00:01Z', finishedAt: '2026-07-01T00:05:01Z',
    queueWaitMs: 1200, wallMs: 300000,
    tts: { provider: 'minimax', characters: 5400 },
    totals: { llmCallCount: 2, inputTokens: 100, outputTokens: 50 },
    stages: [{ node: 'write_script', durationMs: 2000, status: 'ok', attempt: 1 }],
    llmCalls: [
      { node: 'write_script', call: 'segment', attempt: 1, durationMs: 1500, inputTokens: 80, outputTokens: 40, segmentIndex: 0 },
    ],
    research: {
      subtopics: [], providerCounts: { tavily: 3 }, judgeScores: {}, errors: [],
      questionsCount: 4, sourceCount: 3, grounded: true, judgeVerdict: 'pass',
    },
    error: null,
  }
  return {
    getAdminEpisodeStats: vi.fn(async (): Promise<AdminEpisodeStatsResponse> => response),
    getAdminEpisodeGeneration: vi.fn(async (): Promise<AdminEpisodeGeneration> => generation),
    MockAppError: class MockAppError extends Error {},
  }
})

vi.mock('../../api', () => ({
  get api() {
    return { getAdminEpisodeStats, getAdminEpisodeGeneration }
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

  it('點列開啟生成過程 dialog：抓明細、顯示 TTS 供應商與分階段耗時', async () => {
    const { root, container } = await renderPage()
    pendingRoots.push(root)

    expect(getAdminEpisodeGeneration).not.toHaveBeenCalled()
    expect(document.body.textContent).not.toContain('撰寫腳本')

    const row = Array.from(container.querySelectorAll('button')).find(b =>
      b.textContent?.includes('播放少但聽完人數多'),
    )
    await act(async () => {
      row?.click()
    })
    await act(async () => {
      await Promise.resolve()
    })

    expect(getAdminEpisodeGeneration).toHaveBeenCalledWith('ep-quiet')
    expect(document.body.textContent).toContain('生成成功')
    expect(document.body.textContent).toContain('MiniMax')
    expect(document.body.textContent).toContain('撰寫腳本')
    expect(document.body.textContent).toContain('研究過程')
  })
})
