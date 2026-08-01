// @vitest-environment happy-dom
// useSegmentPlayer hook 行為測試：mock HTMLAudioElement + 手動推事件。
// 不裝 @testing-library/react；直接 react-dom/client.createRoot + happy-dom。
// 驗證以「可觀察的 side effect」為主（mock 元素被呼叫、callback 觸發）。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { describe, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useSegmentPlayer } from './useSegmentPlayer'
import type { Episode } from '../types/episode'

interface FakeAudioEl {
  src: string
  currentTime: number
  duration: number
  playbackRate: number
  defaultPlaybackRate: number
  volume: number
  muted: boolean
  paused: boolean
  readyState: number
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
    duration: NaN,
    playbackRate: 1,
    defaultPlaybackRate: 1,
    volume: 1,
    muted: false,
    paused: true,
    readyState: 4,
    listeners,
    play: vi.fn(async () => { el.paused = false; el.dispatch('play'); return undefined }),
    pause: vi.fn(() => { el.paused = true; el.dispatch('pause') }),
    addEventListener: vi.fn((name: string, cb: (e: Event) => void) => {
      if (!listeners.has(name)) listeners.set(name, new Set())
      listeners.get(name)!.add(cb)
    }),
    removeEventListener: vi.fn((name: string, cb: (e: Event) => void) => {
      listeners.get(name)?.delete(cb)
    }),
    dispatch(name: string, e: Event = new Event(name)) {
      for (const cb of Array.from(listeners.get(name) ?? [])) cb(e)
    },
  }
  // 模擬瀏覽器設 src 後立刻觸發載入事件，metadata 相關的 await 才能解開。
  Object.defineProperty(el, 'src', {
    set(v: string) {
      ;(this as { _src: string })._src = v
      this.readyState = 4
      this.duration = 100
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

/** timeupdate 是 currentTime/duration 唯一的事件驅動來源（見 useSegmentPlayer 內
 *  onTimeUpdate），測試模擬播放進度前進都要走這個 helper，不能只改 currentTime。 */
function advanceTime(el: FakeAudioEl, t: number): void {
  el.currentTime = t
  el.dispatch('timeupdate')
}

function makeEpisode(overrides: Partial<Episode> = {}): Episode {
  return {
    id: 'ep-1',
    title: 'Test',
    audioUrl: 'https://cdn/episode.mp3',
    segments: [],
    cues: [
      { index: 0, speaker: 'A', text: 'Hello world', zh: '哈囉世界', start: 0, end: 1, words: [{ word: 'Hello', start: 0, end: 0.4 }, { word: 'world', start: 0.5, end: 1 }] },
      { index: 1, speaker: 'A', text: 'Second line', zh: '第二行', start: 1.3, end: 2.3 },
    ],
    ...overrides,
  }
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

  it('loadEpisode：給合法集數 loadState 變 ready，duration 先用 cues 算（metadata 未到）', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    expect(h.getPlayer().loadState).toBe('ready')
    expect(h.getPlayer().duration).toBe(2.3)
    expect(els[0]!.src).toBe('https://cdn/episode.mp3')
    h.unmount()
  })

  it('loadEpisode：給 null 集數 loadState 變 idle', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(null) })
    expect(h.getPlayer().loadState).toBe('idle')
    h.unmount()
  })

  it('loadEpisode：集數存在但沒有 audioUrl，loadState 變 error', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode({ audioUrl: null })) })
    expect(h.getPlayer().loadState).toBe('error')
    h.unmount()
  })

  it('play：呼叫主播放元素 play()，isPlaying 隨 play 事件變 true', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    await act(async () => { h.getPlayer().play() })
    expect(els[0]!.play).toHaveBeenCalledTimes(1)
    expect(h.getPlayer().isPlaying).toBe(true)
    h.unmount()
  })

  it('pause：isPlaying 隨 pause 事件變 false，不動 currentTime（單一元素天然保留位置）', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    await act(async () => { h.getPlayer().play() })
    await act(async () => { advanceTime(els[0]!, 0.4) })
    expect(h.getPlayer().currentTime).toBe(0.4)

    await act(async () => { h.getPlayer().pause() })
    expect(els[0]!.pause).toHaveBeenCalledTimes(1)
    expect(h.getPlayer().isPlaying).toBe(false)
    expect(h.getPlayer().currentTime).toBe(0.4)

    // 重新 play：原生 <audio> 從 el.currentTime 現在的位置繼續，不需要額外的 offset 邏輯。
    await act(async () => { h.getPlayer().play() })
    expect(els[0]!.currentTime).toBe(0.4)
    h.unmount()
  })

  it('seekTo：呼叫引擎 seek，currentTime 立刻反映（fake 元素 currentTime setter 即時生效）', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    await act(async () => { h.getPlayer().seekTo(1.5) })
    expect(els[0]!.currentTime).toBe(1.5)
    h.unmount()
  })

  it('duration：timeupdate 事件觸發後改讀 el.duration（metadata 到位）', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    expect(h.getPlayer().duration).toBe(2.3) // metadata 未到：cues 推算值
    await act(async () => { advanceTime(els[0]!, 0.1) })
    expect(h.getPlayer().duration).toBe(100) // fake canplay 時已把 duration 設成 100
    h.unmount()
  })

  it('seekToWord：cue 有 words 時跳到 cue.start + word.start 並回 true', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    let ok = false
    act(() => { ok = h.getPlayer().seekToWord(0, 1) })
    expect(ok).toBe(true)
    expect(els[0]!.currentTime).toBeCloseTo(0.5) // cue.start(0) + word[1].start(0.5)
    h.unmount()
  })

  it('seekToWord：cue 沒有 words 時退回 cue.start 並回 false', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    let ok = true
    act(() => { ok = h.getPlayer().seekToWord(1, 0) })
    expect(ok).toBe(false)
    expect(els[0]!.currentTime).toBeCloseTo(1.3) // cue[1].start
    h.unmount()
  })

  it('setPlaybackRate：主播放元素 playbackRate 同步更新', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    await act(async () => { h.getPlayer().setPlaybackRate(1.5) })
    expect(els[0]!.playbackRate).toBe(1.5)
    expect(h.getPlayer().playbackRate).toBe(1.5)
    h.unmount()
  })

  it('setMuted：主播放與試聽元素一起被設', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    await act(async () => { h.getPlayer().setMuted(true) })
    expect(els[0]!.muted).toBe(true)
    expect(els[1]!.muted).toBe(true)
    expect(h.getPlayer().muted).toBe(true)
    h.unmount()
  })

  it('playClip：走試聽元素播放，不影響主播放的 src / currentTime / isPlaying', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    await act(async () => { h.getPlayer().seekTo(0.4) })

    await act(async () => { h.getPlayer().playClip(0, 1) })
    expect(els[1]!.currentTime).toBe(0)
    expect(els[1]!.play).toHaveBeenCalledTimes(1)
    expect(els[0]!.currentTime).toBe(0.4)
    expect(h.getPlayer().isPlaying).toBe(false)
    h.unmount()
  })

  it('ended：整集播完事件觸發後 isPlaying 變 false', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().loadEpisode(makeEpisode()) })
    await act(async () => { h.getPlayer().play() })
    expect(h.getPlayer().isPlaying).toBe(true)
    await act(async () => { els[0]!.dispatch('ended') })
    expect(h.getPlayer().isPlaying).toBe(false)
    h.unmount()
  })

  it('play：沒有載入集數時呼叫是安全 no-op', async () => {
    setupGlobalMock()
    const h = mountHook()
    await act(async () => { h.getPlayer().play() })
    expect(h.getPlayer().isPlaying).toBe(false)
    expect(els.length).toBe(2) // engine 建立時已建好兩顆常駐元素，但沒人呼叫過 play()
    for (const el of els) expect(el.play).not.toHaveBeenCalled()
    h.unmount()
  })
})
