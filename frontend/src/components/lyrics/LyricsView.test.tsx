// @vitest-environment happy-dom
// LyricsView 中／英隱藏 + 點一下揭曉。
//
// 覆蓋的是這個元件唯一一段非平凡邏輯（enHidden/zhHidden × revealedIdx）：
//  - 隱藏的那層打上 blur-sm，且隱藏英文時不再渲染可點的 word span
//    （模糊層底下若留著 span，「點一下揭曉」會先打到某個字去查詞）。
//  - 點模糊行只揭曉該行，不冒泡觸發 onCueClick 的跳句。
//  - 播到下一句時揭曉自動復原。

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, createElement, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { LyricsView } from './LyricsView'
import { VocabContext, type VocabContextValue } from '../../state/vocabContextValue'
import type { Cue } from '../../types/episode'

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const CUES: readonly Cue[] = [
  { index: 0, speaker: 'HOST', text: 'Some people remember dreams.', zh: '有些人記得夢。', start: 0, end: 5 },
  { index: 1, speaker: 'GUEST', text: 'It depends on when you wake.', zh: '取決於你何時醒來。', start: 5, end: 10 },
]

const noop = () => Promise.resolve()
const VOCAB_STUB: VocabContextValue = {
  items: [], isLoading: false, error: null,
  addVocab: noop, removeVocab: noop, clearVocab: noop,
  isInVocab: () => false,
  updateCardReview: noop, completeLearning: noop, applyQuizRound: noop,
  reviveVocab: noop, reload: noop,
}

const pendingRoots: Root[] = []

afterEach(() => {
  for (const r of pendingRoots.splice(0)) act(() => r.unmount())
  document.body.innerHTML = ''
})

function render(node: ReactNode): { rerender: (n: ReactNode) => void; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  pendingRoots.push(root)
  const rerender = (n: ReactNode) => {
    act(() => { root.render(createElement(VocabContext.Provider, { value: VOCAB_STUB }, n)) })
  }
  rerender(node)
  return { rerender, container }
}

function view(props: Partial<Parameters<typeof LyricsView>[0]> = {}): ReactNode {
  return createElement(LyricsView, {
    episodeId: 'ep1', episodeTitle: '測試集', cues: CUES, currentTime: 1,
    onWordClick: () => undefined,
    ...props,
  })
}

/** 找出某句的中文 <p>（每句 wrapper 內第三個子元素）。 */
function zhLine(container: HTMLElement, i: number): HTMLElement {
  const el = container.querySelectorAll('p')[i * 2 + 1]
  if (!(el instanceof HTMLElement)) throw new Error(`no zh line at ${i}`)
  return el
}

function enLine(container: HTMLElement, i: number): HTMLElement {
  const el = container.querySelectorAll('p')[i * 2]
  if (!(el instanceof HTMLElement)) throw new Error(`no en line at ${i}`)
  return el
}

describe('LyricsView 隱藏／揭曉', () => {
  it('showZh=false：中文模糊，英文仍保有可點的 word span', () => {
    const { container } = render(view({ showZh: false }))
    expect(zhLine(container, 0).className).toContain('blur-sm')
    expect(zhLine(container, 0).textContent).toBe('有些人記得夢。')
    // 英文沒關 → renderTokenized 照跑，字還是可點的
    expect(enLine(container, 0).querySelectorAll('span').length).toBeGreaterThan(0)
  })

  it('showEn=false：英文模糊且退回純文字，不留可點 span', () => {
    const { container } = render(view({ showEn: false }))
    const en = enLine(container, 0)
    expect(en.className).toContain('blur-sm')
    expect(en.textContent).toBe('Some people remember dreams.')
    expect(en.querySelectorAll('span').length).toBe(0)
  })

  it('點模糊行只揭曉該行，不觸發 onCueClick 跳句', () => {
    const onCueClick = vi.fn()
    const { container } = render(view({ showZh: false, onCueClick }))
    act(() => { zhLine(container, 0).dispatchEvent(new MouseEvent('click', { bubbles: true })) })

    expect(zhLine(container, 0).className).not.toContain('blur-sm')
    expect(onCueClick).not.toHaveBeenCalled()
    // 只有被點的那一行揭曉，其他句維持模糊
    expect(zhLine(container, 1).className).toContain('blur-sm')
  })

  it('播到下一句時揭曉自動復原', () => {
    const { container, rerender } = render(view({ showZh: false }))
    act(() => { zhLine(container, 0).dispatchEvent(new MouseEvent('click', { bubbles: true })) })
    expect(zhLine(container, 0).className).not.toContain('blur-sm')

    rerender(view({ showZh: false, currentTime: 6 }))
    expect(zhLine(container, 0).className).toContain('blur-sm')
  })
})
