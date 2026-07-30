// @vitest-environment happy-dom
// 智慧佇列 session 整合測試：mock useVocab 回傳已知 items，驗證 SessionRoute 行為。
// 不對外實作細節（SwipeCard 內部 spring、選擇題干擾項），只測「commit / 結算頁」這條
// 可見通路。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter } from 'react-router-dom'
import { SessionRoute } from './SessionRoute'
import type { VocabItem } from '../api/types'

const TODAY = '2026-07-30'
function items(): VocabItem[] {
  return [
    {
      id: 'learn-1',
      word: 'apple',
      lemma: 'apple',
      translation: '蘋果',
      sourceEpisodeId: 'ep-1',
      sourceLineNo: 0,
      sourceTimestamp: 0,
      createdAt: '2026-01-01T00:00:00Z',
      senseIdx: 0,
      status: 1,
    },
    {
      id: 'review-1',
      word: 'banana',
      lemma: 'banana',
      translation: '香蕉',
      sourceEpisodeId: 'ep-1',
      sourceLineNo: 1,
      sourceTimestamp: 1,
      createdAt: '2026-01-02T00:00:00Z',
      senseIdx: 0,
      status: 2,
      interval: 6,
      nextReview: TODAY, // 到期 → 走 recognize
    },
    {
      id: 'quiz-1',
      word: 'cherry',
      lemma: 'cherry',
      translation: '櫻桃',
      sourceEpisodeId: 'ep-1',
      sourceLineNo: 2,
      sourceTimestamp: 2,
      createdAt: '2026-01-03T00:00:00Z',
      senseIdx: 0,
      status: 2,
      interval: 21,
      nextReview: TODAY, // 到期 + interval ≥ 21 → 走 quiz
    },
  ]
}

const state: { items: VocabItem[]; isLoading: boolean; error: string | null } = {
  items: [],
  isLoading: false,
  error: null,
}

const completeLearning = vi.fn(async () => undefined)
const updateCardReview = vi.fn(async () => undefined)
const applyQuizRound = vi.fn(async () => undefined)
const reload = vi.fn(async () => undefined)

vi.mock('../state', () => ({
  useVocab: () => ({
    items: state.items,
    isLoading: state.isLoading,
    error: state.error,
    reload,
    addVocab: vi.fn(),
    removeVocab: vi.fn(),
    clearVocab: vi.fn(),
    isInVocab: () => false,
    updateCardReview,
    completeLearning,
    applyQuizRound,
    reviveVocab: vi.fn(),
  }),
}))

const pending: Root[] = []

async function render(): Promise<HTMLDivElement> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  pending.push(root)
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={['/session']}>
        <SessionRoute />
      </MemoryRouter>,
    )
  })
  return container
}

afterEach(() => {
  pending.forEach(r => r.unmount())
  pending.length = 0
  completeLearning.mockClear()
  updateCardReview.mockClear()
  applyQuizRound.mockClear()
  reload.mockClear()
})

beforeEach(() => {
  // 用「今天」當基準：測試要跟同一天跑出穩定結果
  state.items = items()
  state.isLoading = false
  state.error = null
  vi.spyOn(Date.prototype, 'toLocaleDateString').mockImplementation(function () { return TODAY })
})

describe('SessionRoute', () => {
  it('載入完成 + 佇列非空 → 進度與第一張卡片', async () => {
    const container = await render()
    const text = container.textContent ?? ''
    expect(text).toContain('第 1 / 3 張')
    // 第一張卡是到期的 review 卡（banana），不是新字 apple——佇列規則 due 優先
    expect(text).toContain('banana')
  })

  it('items 為空 → 顯示「單字本是空的」空狀態', async () => {
    state.items = []
    const container = await render()
    expect(container.textContent ?? '').toContain('單字本是空的')
  })
})
