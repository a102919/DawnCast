// @vitest-environment happy-dom
// AdminRoute：Token 用量面板（分階段耗時展開/收合）回歸鎖。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { AdminRoute } from './AdminRoute'
import type { AdminTokenUsageResponse } from '../api'

const { getAdminTokenUsage, MockAppError } = vi.hoisted(() => {
  const mockResponse: AdminTokenUsageResponse = {
    totalInputTokens: 300,
    totalOutputTokens: 150,
    episodeCount: 2,
    items: [
      {
        slug: 'ep-1',
        title: '第一集：有分階段耗時',
        inputTokens: 100,
        outputTokens: 50,
        createdAt: '2026-07-01T00:00:00Z',
        generationStartedAt: '2026-07-01T00:00:00Z',
        generationFinishedAt: '2026-07-01T00:05:00Z',
        stages: [{ node: 'write_script', durationMs: 5000, status: 'ok', attempt: 1 }],
      },
      {
        slug: 'ep-2',
        title: '第二集：無 metrics',
        inputTokens: 200,
        outputTokens: 100,
        createdAt: '2026-07-02T00:00:00Z',
        generationStartedAt: null,
        generationFinishedAt: null,
        stages: [],
      },
    ],
  }
  return {
    getAdminTokenUsage: vi.fn(async (): Promise<AdminTokenUsageResponse> => mockResponse),
    MockAppError: class MockAppError extends Error {},
  }
})

vi.mock('../api', () => ({
  get api() {
    return { getAdminTokenUsage }
  },
  AppError: MockAppError,
  getAdminToken: () => 'test-admin-token',
  setAdminToken: vi.fn(),
  clearAdminToken: vi.fn(),
}))

async function renderRoute(): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/admin']}>
        <AdminRoute />
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

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('AdminRoute：Token 用量面板', () => {
  it('已有 admin token 時自動載入並顯示總量 + 明細', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    expect(getAdminTokenUsage).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('第一集：有分階段耗時')
    expect(container.textContent).toContain('第二集：無 metrics')
    // stage 明細預設收合，不該直接出現在畫面上
    expect(container.textContent).not.toContain('write_script')
  })

  it('點有 stages 的列會展開分階段耗時；沒有 stages 的列點了沒反應', async () => {
    const { root, container } = await renderRoute()
    pendingRoots.push(root)

    const buttons = Array.from(container.querySelectorAll('button')).filter(b =>
      b.textContent?.includes('第一集') || b.textContent?.includes('第二集'),
    )
    expect(buttons).toHaveLength(2)
    const [withStages, withoutStages] = buttons

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
