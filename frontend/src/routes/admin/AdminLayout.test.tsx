// @vitest-environment happy-dom
// AdminLayout：側邊導覽 + 唯一的權杖判斷點。
//
// 覆蓋：/admin 導向 /admin/episodes；側邊欄渲染出兩個導覽項目且目前頁面標記為
// active；沒有權杖時內容區顯示權杖提示、不掛載子頁面（子頁面不掛載＝不會打 API，
// 這是本檔真正要證明的事，而不是另外數一次 fetch 呼叫次數）。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Navigate, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminLayout } from './AdminLayout'

const { getAdminToken, MockAppError } = vi.hoisted(() => ({
  getAdminToken: vi.fn((): string | null => null),
  MockAppError: class MockAppError extends Error {},
}))

vi.mock('../../api', () => ({
  getAdminToken,
  setAdminToken: vi.fn(),
  clearAdminToken: vi.fn(),
  AppError: MockAppError,
}))

function EpisodesProbe() {
  return <div>單集數據子頁面內容</div>
}

function ChannelsProbe() {
  return <div>頻道管理子頁面內容</div>
}

// 複製 App.tsx 實際的巢狀路由形狀（index 重導 + 兩個子路由），用假子頁面
// 取代真正的 EpisodesPage/ChannelsPage——本檔只驗 Layout 這一層的行為。
async function renderLayout(initialPath = '/admin/episodes'): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<Navigate to="episodes" replace />} />
            <Route path="episodes" element={<EpisodesProbe />} />
            <Route path="channels" element={<ChannelsProbe />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )
  })

  return { root, container }
}

const pendingRoots: Root[] = []

afterEach(() => {
  for (const r of pendingRoots.splice(0)) r.unmount()
  document.body.innerHTML = ''
  vi.clearAllMocks()
})

describe('AdminLayout', () => {
  it('/admin 導向 /admin/episodes', async () => {
    getAdminToken.mockReturnValue('test-token')
    const { root, container } = await renderLayout('/admin')
    pendingRoots.push(root)

    expect(container.textContent).toContain('單集數據子頁面內容')
  })

  it('側邊欄渲染兩個導覽項目，目前頁面標記為 active', async () => {
    getAdminToken.mockReturnValue('test-token')
    const { root, container } = await renderLayout('/admin/episodes')
    pendingRoots.push(root)

    expect(container.textContent).toContain('單集數據')
    expect(container.textContent).toContain('頻道管理')

    const links = Array.from(container.querySelectorAll('a'))
    const episodesLink = links.find(a => a.getAttribute('href') === '/admin/episodes')
    const channelsLink = links.find(a => a.getAttribute('href') === '/admin/channels')
    expect(episodesLink?.className).toContain('text-accent')
    expect(channelsLink?.className).not.toContain('text-accent')
  })

  it('沒有權杖時內容區顯示提示，不掛載子頁面', async () => {
    getAdminToken.mockReturnValue(null)
    const { root, container } = await renderLayout('/admin/episodes')
    pendingRoots.push(root)

    expect(container.textContent).toContain('管理員權杖')
    expect(container.textContent).not.toContain('單集數據子頁面內容')
  })

  it('有權杖時渲染對應子頁面', async () => {
    getAdminToken.mockReturnValue('test-token')
    const { root, container } = await renderLayout('/admin/channels')
    pendingRoots.push(root)

    expect(container.textContent).toContain('頻道管理子頁面內容')
  })
})
