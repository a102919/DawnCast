// @vitest-environment happy-dom
// ChannelsPage：頻道清單 / 選題庫載入 / 建立表單的邊界驗證。
//
// 權杖是否存在的判斷已上移到 AdminLayout（見 AdminLayout.test.tsx），
// 本頁不再處理 hasToken，一律假設已經有權杖才會被掛載。
//
// 「候選數」是這個面板唯一會影響隔天有沒有出刊的數字，所以它一定要被渲染出來；
// slug 格式驗證在前端擋，是因為格式錯的 slug 會撞後端 unique index 炸成 500。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { ChannelsPage } from './ChannelsPage'
import type { Channel, ChannelTopic } from '../../api'

const { listAdminChannels, listAdminChannelTopics, createAdminChannel, MockAppError } = vi.hoisted(() => {
  const channels: readonly Channel[] = [
    {
      id: 'ch-1', slug: 'ai-at-work', name: 'AI 工作現場', themePrompt: '聚焦 AI agent 的真實應用',
      topic: 'tech', topicType: 'news', lengthTier: 'medium', cefrLevel: 'B1',
      targetIntervalDays: 3, status: 'active', episodeCount: 4, candidateCount: 2,
    },
    {
      id: 'ch-2', slug: 'dry-well', name: '選題庫乾掉的頻道', themePrompt: '測試用',
      topic: 'science', topicType: 'evergreen', lengthTier: 'short', cefrLevel: 'A2',
      targetIntervalDays: 7, status: 'paused', episodeCount: 0, candidateCount: 0,
    },
  ]
  const topics: readonly ChannelTopic[] = [
    {
      id: 't-1', channelId: 'ch-1', canonicalTopic: 'AI 客服的真實成本', angle: '應用場景',
      score: 0.82, status: 'candidate', createdAt: '2026-07-28T01:00:00Z',
    },
  ]
  return {
    listAdminChannels: vi.fn(async (): Promise<readonly Channel[]> => channels),
    listAdminChannelTopics: vi.fn(async (): Promise<readonly ChannelTopic[]> => topics),
    createAdminChannel: vi.fn(),
    MockAppError: class MockAppError extends Error {},
  }
})

vi.mock('../../api', () => ({
  get api() {
    return { listAdminChannels, listAdminChannelTopics, createAdminChannel }
  },
  AppError: MockAppError,
}))

/** React 受控輸入：要走 native setter 才會觸發 onChange。 */
function setInput(el: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll('button')).find(b => b.textContent?.includes(text))
  if (!btn) throw new Error(`找不到按鈕：${text}`)
  return btn
}

async function renderPanel(): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(
      <MemoryRouter>
        <ChannelsPage />
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
  vi.clearAllMocks()
})

describe('ChannelsPage', () => {
  it('列出頻道並顯示狀態與候選數（候選 0 代表隔天不會出刊）', async () => {
    const { root, container } = await renderPanel()
    pendingRoots.push(root)

    expect(listAdminChannels).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('AI 工作現場')
    expect(container.textContent).toContain('啟用中')
    expect(container.textContent).toContain('候選 2')
    expect(container.textContent).toContain('選題庫乾掉的頻道')
    expect(container.textContent).toContain('已暫停')
    expect(container.textContent).toContain('候選 0')
    // 選題庫沒展開之前不該被載入。
    expect(listAdminChannelTopics).not.toHaveBeenCalled()
  })

  it('展開頻道才載入選題庫，並顯示候選與分數', async () => {
    const { root, container } = await renderPanel()
    pendingRoots.push(root)

    await act(async () => {
      findButton(container, 'AI 工作現場').click()
    })
    await act(async () => {
      await Promise.resolve()
    })

    expect(listAdminChannelTopics).toHaveBeenCalledWith('ch-1')
    expect(container.textContent).toContain('AI 客服的真實成本')
    expect(container.textContent).toContain('0.82')
    expect(container.textContent).toContain('聚焦 AI agent 的真實應用')
  })

  it('slug 格式不合時不送出請求，直接顯示錯誤', async () => {
    const { root, container } = await renderPanel()
    pendingRoots.push(root)

    await act(async () => {
      findButton(container, '新增頻道').click()
    })

    const inputs = Array.from(container.querySelectorAll('input[type="text"]'))
    const textarea = container.querySelector('textarea')
    expect(inputs.length).toBeGreaterThanOrEqual(2)
    expect(textarea).not.toBeNull()

    await act(async () => {
      setInput(inputs[0] as HTMLInputElement, '測試頻道')
      setInput(inputs[1] as HTMLInputElement, 'Bad Slug!')
      setInput(textarea as HTMLTextAreaElement, '這個頻道要寫什麼')
    })

    await act(async () => {
      findButton(container, '建立頻道').click()
    })

    expect(createAdminChannel).not.toHaveBeenCalled()
    expect(container.textContent).toContain('代稱只能用小寫英文、數字與連字號')
  })
})
