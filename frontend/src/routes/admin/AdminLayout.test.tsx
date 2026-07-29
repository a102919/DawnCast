// @vitest-environment happy-dom
// AdminLayout：側邊導覽 + 子頁面掛載點。
//
// 覆蓋：/admin 導向 /admin/episodes；側邊欄渲染出兩個導覽項目且目前頁面標記為
// active。X-Admin-Token 後門已於 2026-07-29 砍掉，layout 不再負責權杖判斷——
// 子頁面一律掛載,沒通過後端認證會由 request() 拿 401。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Navigate, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import { AdminLayout } from './AdminLayout'

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
})

describe('AdminLayout', () => {
  it('/admin 導向 /admin/episodes', async () => {
    const { root, container } = await renderLayout('/admin')
    pendingRoots.push(root)

    expect(container.textContent).toContain('單集數據子頁面內容')
  })

  it('側邊欄渲染兩個導覽項目,目前頁面標記為 active', async () => {
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

  it('/admin/channels 渲染對應子頁面', async () => {
    const { root, container } = await renderLayout('/admin/channels')
    pendingRoots.push(root)

    expect(container.textContent).toContain('頻道管理子頁面內容')
  })
})
