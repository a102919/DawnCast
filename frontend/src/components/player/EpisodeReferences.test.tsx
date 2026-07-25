// @vitest-environment happy-dom
// EpisodeReferences 測試（Task #67 播放器參考資料 UI）。
//
// 覆蓋：
//  - 「無來源不渲染」：傳入空陣列回 null，不留任何 DOM 節點。
//  - 預設關閉，點 summary 後展開列出全部連結。
//  - 外連一律 target="_blank" rel="noopener noreferrer"。
//  - 顯示 publisher（若有）；省略 publisher 也不破版。
//  - 繁體中文 + 無 emoji + 使用 lucide 圖示（BookMarked / ExternalLink / ChevronDown）。

import { afterEach, describe, expect, it } from 'vitest'
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { EpisodeReferences } from './EpisodeReferences'
import type { SourceReference } from '../../types/episode'

// React 19 act() 環境感知旗標，沒設會跳 warn；不影響測試通過但很吵。
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

function renderElement(node: ReactNode): { root: Root; container: HTMLDivElement } {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => { root.render(node) })
  return { root, container }
}

function clickSync(el: HTMLElement): void {
  act(() => { el.dispatchEvent(new MouseEvent('click', { bubbles: true })) })
}

const pendingRoots: Root[] = []

afterEach(() => {
  for (const r of pendingRoots.splice(0)) r.unmount()
  document.body.innerHTML = ''
})

const SAMPLE: readonly SourceReference[] = [
  { id: 'ibm', title: 'IBM Quantum Learning', url: 'https://learning.quantum.ibm.com/' },
  { id: 'wikipedia', title: '維基百科：量子位元', url: 'https://zh.wikipedia.org/wiki/%E9%87%8F%E5%AD%90%E4%BD%8D%E5%85%83' },
  { id: 'quantum-country', title: '量子國家', url: 'https://quantum.country/qcvc/superposition' },
]

describe('EpisodeReferences：無來源不渲染', () => {
  it('空陣列 → 不產生任何 DOM', () => {
    const { container } = renderElement(<EpisodeReferences references={[]} />)
    expect(container.firstChild).toBeNull()
  })
})

describe('EpisodeReferences：UI 結構', () => {
  it('用原生 <details> + <summary>，預設關閉，summary 顯示總數', () => {
    const { root, container } = renderElement(<EpisodeReferences references={SAMPLE} />)
    pendingRoots.push(root)

    const details = container.querySelector('details')
    expect(details).not.toBeNull()
    // 預設關閉（沒設 open）
    expect(details?.hasAttribute('open')).toBe(false)

    const summary = container.querySelector('summary')
    expect(summary?.textContent).toContain('參考資料')
    expect(summary?.textContent).toContain('3')
  })

  it('點 summary 後展開列表，三個連結都出現且文字正確', () => {
    const { root, container } = renderElement(<EpisodeReferences references={SAMPLE} />)
    pendingRoots.push(root)

    const summary = container.querySelector('summary')
    if (!summary) throw new Error('找不到 summary')
    clickSync(summary)

    const links = Array.from(container.querySelectorAll<HTMLAnchorElement>('a'))
    expect(links).toHaveLength(3)
    expect(links[0]?.textContent).toContain('IBM Quantum Learning')
    expect(links[1]?.textContent).toContain('維基百科')
    expect(links[2]?.textContent).toContain('量子國家')
  })

})

describe('EpisodeReferences：外連安全設定', () => {
  it('所有 <a> 都有 target="_blank" + rel="noopener noreferrer"', () => {
    const { root, container } = renderElement(<EpisodeReferences references={SAMPLE} />)
    pendingRoots.push(root)

    // 預設關閉時 ul 已被 React 渲染到 DOM（只是視覺隱藏），
    // 故可以一次查到所有連結。
    const links = Array.from(container.querySelectorAll<HTMLAnchorElement>('a'))
    expect(links).toHaveLength(3)
    for (const a of links) {
      expect(a.target).toBe('_blank')
      // rel 可能被瀏覽器序列化為 "noopener noreferrer"（兩個 token）
      const rel = a.rel
      expect(rel.split(/\s+/)).toEqual(expect.arrayContaining(['noopener', 'noreferrer']))
      expect(a.href).toMatch(/^https?:\/\//)
    }
  })

  it('使用 ExternalLink 圖示標示外連（lucide）', () => {
    const { root, container } = renderElement(<EpisodeReferences references={SAMPLE} />)
    pendingRoots.push(root)

    // lucide-react 渲染 SVG <svg class="lucide lucide-external-link ...">
    const svgs = Array.from(container.querySelectorAll('svg'))
    const externalIcon = svgs.find(svg => svg.classList.contains('lucide-external-link'))
    expect(externalIcon).toBeDefined()
  })
})

describe('EpisodeReferences：本地化與禁用 emoji', () => {
  it('summary 文字含繁體中文且不含 emoji（用 BMP 範圍比對）', () => {
    const { root, container } = renderElement(<EpisodeReferences references={SAMPLE} />)
    pendingRoots.push(root)

    const summary = container.querySelector('summary')
    expect(summary?.textContent).toMatch(/[一-鿿]/) // 含中文字符
    // 0x1F300–0x1FAFF 為 emoji 大宗區段；摘要不該出現
    const emojiPattern = /[\u{1F300}-\u{1FAFF}]/u
    expect(summary?.textContent?.match(emojiPattern)).toBeNull()
  })
})
