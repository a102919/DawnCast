// @vitest-environment happy-dom
// useSegmentPlayer hook 行為測試：mock HTMLAudioElement + 手動推事件。
// 不裝 @testing-library/react；直接 react-dom/client.createRoot + happy-dom。
// 驗證以「可觀察的 side effect」為主（mock 元素被呼叫、callback 觸發）。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { describe, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useSegmentPlayer } from './useSegmentPlayer'
import type { Episode, Segment } from '../types/episode'

interface FakeAudioEl {
  src: string
  currentTime: number
  playbackRate: number
  defaultPlaybackRate: number
  volume: number
  muted: boolean
  paused: boolean
  readyState: number
  onended: ((e: Event) => void) | null
  listeners: Map<string, Set<(e: Event) => void>>
  play: ReturnType<typeof vi.fn>
  pause: ReturnType<typeof vi.fn>
  addEventListener: ReturnType<typeof vi.fn>
  removeEventListener: ReturnType<typeof vi.fn>
  dispatch(name: string, e?: Event): void
}

function makeFakeAudio(): FakeAudioEl {
  const listeners = new Map<string, Set<(e: Event) => void>>()
  const el: FakeAudioEl = {
    src: '',
    currentTime: 0,
    playbackRate: 1,
    defaultPlaybackRate: 1,
    volume: 1,
    muted: false,
    paused: true,
    readyState: 4,
    onended: null,
    listeners,
    play: vi.fn(async () => { el.paused = false; return undefined }),
    pause: vi.fn(() => { el.paused = true }),
    addEventListener: vi.fn((name: string, cb: (e: Event) => void) => {
      if (!listeners.has(name)) listeners.set(name, new Set())
      listeners.get(name)!.add(cb)
    }),
    removeEventListener: vi.fn((name: string, cb: (e: Event) => void) => {
      listeners.get(name)?.delete(cb)
    }),
    dispatch(name: string, e: Event = new Event(name)) {
      listeners.get(name)?.forEach(cb => cb(e))
      if (name === 'ended') this.onended?.(e)
    },
  }
  // 模擬瀏覽器設 src 後立刻觸發載入事件，preload 一類的 await 才能解開。
  Object.defineProperty(el, 'src', {
    set(v: string) {
      ;(this as { _src: string })._src = v
      this.readyState = 4
      this.dispatch('loadedmetadata')
      this.dispatch('loadeddata')
      this.dispatch('canplay')
    },
    get() { return (this as { _src: string })._src ?? '' },
  })
  return el
}

const els: FakeAudioEl[] = []

function setupGlobalMock() {
  els.length = 0
  ;(window as unknown as { Audio: unknown }).Audio = vi.fn(() => {
    const e = makeFakeAudio()
    els.push(e)
    return e
  })
}

function makeEpisode(n: number, prefix = 'https://cdn/'): Episode {
  const segments: Segment[] = []
  const cues: { index: number; speaker: string; text: string; zh: string; start: number; end: number }[] = []
  let t = 0
  for (let i = 0; i < n; i++) {
    const dur = 1.0
    segments.push({ index: i, audioUrl: `${prefix}${i}.mp3`, duration: dur, start: t, end: t + dur })
    cues.push({ index: i, speaker: 'A', text: `line ${i}`, zh: `第 ${i} 行`, start: t, end: t + dur })
    t += dur + 0.1
  }
  return { id: 'ep-1', title: 'Test', audioUrl: null, segments, cues }
}

function mountHook(): { getPlayer: () => ReturnType<typeof useSegmentPlayer>; unmount: () => void } {
  let lastPlayer: ReturnType<typeof useSegmentPlayer> | null = null
  function Probe() {
    lastPlayer = useSegmentPlayer()
    return null
  }
  const container = document.createElement('div')
  const root: Root = createRoot(container)
  act(() => {
    root.render(createElement(Probe))
  })
  return {
    getPlayer: () => {
      if (!lastPlayer) throw new Error('hook not yet mounted')
      return lastPlayer
    },
    unmount: () => {
      act(() => root.unmount())
    },
  }
}

describe('useSegmentPlayer', () => {
  it('initially returns idle state', () => {
    setupGlobalMock()
    const h = mountHook()
    const p = h.getPlayer()
    expect(p.loadState).toBe('idle')
    expect(p.isPlaying).toBe(false)
    expect(p.duration).toBe(0)
    expect(p.muted).toBe(false)
    h.unmount()
  })

  it('loadEpisode：給合法集數 loadState 變 ready，並算出 duration', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    expect(h.getPlayer().loadState).toBe('ready')
    expect(h.getPlayer().duration).toBeGreaterThan(0)
    h.unmount()
  })

  it('loadEpisode：給 null/空集數 loadState 變 idle', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(null) })
    expect(h.getPlayer().loadState).toBe('idle')
    h.unmount()
  })

  it('unlock 會在所有元素上 play+pause（iOS 同步授權）', () => {
    setupGlobalMock()
    const h = mountHook()
    h.getPlayer().unlock()
    expect(els.length).toBe(3)
    for (const el of els) {
      expect(el.play).toHaveBeenCalled()
      expect(el.pause).toHaveBeenCalled()
    }
    h.unmount()
  })

  it('play：把 src 設進主播放元素，從 0 起播，isPlaying 變 true', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    await act(async () => { h.getPlayer().play() })
    const main = els[0]!
    expect(main.src).toContain('https://cdn/0.mp3')
    expect(main.currentTime).toBe(0)
    expect(main.play).toHaveBeenCalled()
    expect(h.getPlayer().isPlaying).toBe(true)
    h.unmount()
  })

  it('pause 後 play 從暫停位置接續，不是從段落開頭重播', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(2)) })
    await act(async () => { h.getPlayer().play() })
    const main = els[0]!
    expect(main.currentTime).toBe(0)

    main.currentTime = 0.4
    await act(async () => { h.getPlayer().pause() })
    expect(main.pause).toHaveBeenCalled()

    await act(async () => { h.getPlayer().play() })
    expect(main.currentTime).toBeCloseTo(0.4)
    h.unmount()
  })

  it('playSegment 不動主播放游標：試聽別段後續播，仍從原本暫停的位置接續', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    await act(async () => { h.getPlayer().play() })
    const main = els[0]!
    main.currentTime = 0.4
    await act(async () => { h.getPlayer().pause() })

    await act(async () => { h.getPlayer().playSegment(2, 0.3, 0.5) })
    expect(els[2]!.src).toContain('https://cdn/2.mp3')
    expect(main.src).toContain('https://cdn/0.mp3')

    await act(async () => { h.getPlayer().play() })
    expect(main.currentTime).toBeCloseTo(0.4)
    h.unmount()
  })

  it('playSegment 播完不動全域 isPlaying，也不會自動接播下一段', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    await act(async () => { h.getPlayer().playSegment(0, 0, 0.5) })
    expect(h.getPlayer().isPlaying).toBe(false)
    els[2]!.dispatch('ended')
    expect(h.getPlayer().isPlaying).toBe(false)
    h.unmount()
  })

  it('自動接播：onended 觸發後等 gap 時間到才換下一段', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    await act(async () => { h.getPlayer().play() })
    const main = els[0]!
    expect(main.src).toContain('https://cdn/0.mp3')
    expect(h.getPlayer().isPlaying).toBe(true)

    await act(async () => { main.dispatch('ended') })
    expect(main.src).toContain('https://cdn/0.mp3') // 馬上就看到換就壞了

    await act(async () => {
      await new Promise(r => setTimeout(r, 150))
    })
    expect(els[1]!.src).toContain('https://cdn/1.mp3')
    expect(h.getPlayer().isPlaying).toBe(true)
    h.unmount()
  })

  it('seekTo：找到對應段並從指定 offset 開始', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    await act(async () => { h.getPlayer().play() })
    await act(async () => { h.getPlayer().seekTo(2.3) }) // 落在第 2 段（start=2.2）
    const main = els[0]!
    expect(main.src).toContain('https://cdn/2.mp3')
    expect(main.currentTime).toBeCloseTo(0.1)
    h.unmount()
  })

  it('setPlaybackRate 即時更新主播放元素', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    await act(async () => { h.getPlayer().play() })
    const main = els[0]!
    expect(main.playbackRate).toBe(1)
    await act(async () => { h.getPlayer().setPlaybackRate(1.5) })
    expect(main.playbackRate).toBe(1.5)
    h.unmount()
  })

  it('setMuted：三個元素 muted 一起被設', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(3)) })
    await act(async () => { h.getPlayer().setMuted(true) })
    for (const el of els) expect(el.muted).toBe(true)
    expect(h.getPlayer().muted).toBe(true)
    h.unmount()
  })

  it('整集播完：loadEpisode 給的 segments 全播完後 isPlaying 變 false', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode(2)) })
    await act(async () => { h.getPlayer().play() })
    expect(els[0]!.src).toContain('https://cdn/0.mp3')
    await act(async () => { els[0]!.dispatch('ended') })
    await act(async () => { await new Promise(r => setTimeout(r, 150)) })
    expect(els[1]!.src).toContain('https://cdn/1.mp3')
    await act(async () => { els[1]!.dispatch('ended') })
    expect(h.getPlayer().isPlaying).toBe(false)
    h.unmount()
  })
})
