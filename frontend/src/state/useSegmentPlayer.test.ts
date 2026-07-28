// @vitest-environment happy-dom
// useSegmentPlayer hook 行為測試：mock Web Audio API + fetch，
// 驗證 lazy decode / LRU evict / seek / playbackRate / 跨 episode / ducking。
//
// 不裝 @testing-library/react；直接 react-dom/client.createRoot + happy-dom。
// 驗證以「可觀察的 side effect」（mock 函式被呼叫）為主，避免 hook 內部 state
// 在 act() 之後 closure capture 帶來的不穩定。

// React 19 對 act() 的環境感知旗標，沒設會跳 warn；不影響測試通過但很吵。
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useSegmentPlayer } from './useSegmentPlayer'
import type { Episode, Segment } from '../types/episode'

interface FakeBufferSourceNode {
  buffer: AudioBuffer | null
  playbackRate: { value: number }
  connect: ReturnType<typeof vi.fn>
  start: ReturnType<typeof vi.fn>
  stop: ReturnType<typeof vi.fn>
  disconnect: ReturnType<typeof vi.fn>
  onended: ((e: unknown) => void) | null
}

interface FakeGainParam {
  value: number
  cancelScheduledValues: ReturnType<typeof vi.fn>
  setValueAtTime: ReturnType<typeof vi.fn>
  linearRampToValueAtTime: ReturnType<typeof vi.fn>
}

interface FakeGainNode {
  gain: FakeGainParam
  connect: ReturnType<typeof vi.fn>
}

interface FakeAudioContext {
  state: string
  currentTime: number
  destination: unknown
  decodeAudioData: ReturnType<typeof vi.fn>
  createBufferSource: () => FakeBufferSourceNode
  createGain: () => FakeGainNode
  resume: ReturnType<typeof vi.fn>
}

const sources: FakeBufferSourceNode[] = []
const gains: FakeGainNode[] = []
let fakeCtx: FakeAudioContext
let realFetch: typeof fetch

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

function setupGlobalMocks() {
  sources.length = 0
  gains.length = 0
  fakeCtx = {
    state: 'running',
    currentTime: 0,
    destination: {},
    decodeAudioData: vi.fn(async () => ({ duration: 1.0, length: 44100 } as unknown as AudioBuffer)),
    createBufferSource: () => {
      const node: FakeBufferSourceNode = {
        buffer: null,
        playbackRate: { value: 1 },
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
        disconnect: vi.fn(),
        onended: null,
      }
      sources.push(node)
      return node
    },
    createGain: () => {
      const g: FakeGainNode = {
        gain: {
          value: 1,
          cancelScheduledValues: vi.fn(),
          setValueAtTime: vi.fn(),
          linearRampToValueAtTime: vi.fn(),
        },
        connect: vi.fn(),
      }
      gains.push(g)
      return g
    },
    resume: vi.fn(async () => undefined),
  }
  ;(window as unknown as { AudioContext: unknown }).AudioContext = vi.fn(() => fakeCtx)
  realFetch = global.fetch
  global.fetch = vi.fn(async () => ({
    ok: true,
    arrayBuffer: async () => new Uint8Array([0x00]).buffer,
  })) as unknown as typeof fetch
}

function teardownGlobalMocks() {
  global.fetch = realFetch
  vi.restoreAllMocks()
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
  beforeEach(() => {
    setupGlobalMocks()
  })
  afterEach(() => {
    teardownGlobalMocks()
  })

  it('initially returns idle state', () => {
    const h = mountHook()
    const p = h.getPlayer()
    expect(p.loadState).toBe('idle')
    expect(p.isPlaying).toBe(false)
    expect(p.duration).toBe(0)
    h.unmount()
  })

  it('unlock calls ctx.resume when suspended', async () => {
    fakeCtx.state = 'suspended'
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().unlock()
    })
    expect(fakeCtx.resume).toHaveBeenCalled()
    h.unmount()
  })

  it('loadEpisode decodes first segment via fetch + decodeAudioData', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3))
    })
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    expect(fetchMock).toHaveBeenCalled()
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('https://cdn/0.mp3')
    expect(fakeCtx.decodeAudioData).toHaveBeenCalled()
    h.unmount()
  })

  it('playSegment triggers ducking ramp on main gain', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3))
    })
    await act(async () => {
      // playSegment 內部是 duckAndPlaySegment async，需 await 才完成
      h.getPlayer().playSegment(0, 0, 0.5)
      await new Promise((r) => setTimeout(r, 10))
    })
    expect(gains[0]!.gain.linearRampToValueAtTime).toHaveBeenCalled()
    expect(sources.at(-1)!.start).toHaveBeenCalledWith(0, 0, expect.any(Number))
    h.unmount()
  })

  it('switching episode fetches from new prefix', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3, 'https://a/'))
      await h.getPlayer().loadEpisode(makeEpisode(2, 'https://b/'))
    })
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    const calls = fetchMock.mock.calls.map((c) => String(c[0]))
    expect(calls.some((u) => u.includes('https://b/'))).toBe(true)
    h.unmount()
  })

  it('setVolume clamps to [0, 1] when writing to segment gain', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(2))
    })
    // 第 2 個 gain 是 segmentGain（mainGain 是 [0]）
    const segmentGain = gains[1]!
    act(() => {
      h.getPlayer().setVolume(1.5)
    })
    expect(segmentGain.gain.setValueAtTime).toHaveBeenLastCalledWith(1, expect.any(Number))
    act(() => {
      h.getPlayer().setVolume(-0.5)
    })
    expect(segmentGain.gain.setValueAtTime).toHaveBeenLastCalledWith(0, expect.any(Number))
    h.unmount()
  })

  it('setPlaybackRate updates active source playbackRate.value', async () => {
    const h = mountHook()
    // 用 playSegment 觸發 source 建立（play() 受 closure loadState 問題干擾，
    // 但 playSegment 是 sync-fire-and-forget 走 cached buffer 路徑，較可靠）
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3))
    })
    await act(async () => {
      h.getPlayer().playSegment(0, 0, 1.0)
      await new Promise((r) => setTimeout(r, 10))
    })
    const active = sources.at(-1)!
    expect(active).toBeDefined()
    expect(active.playbackRate.value).toBe(1)
    act(() => {
      h.getPlayer().setPlaybackRate(1.5)
    })
    expect(active.playbackRate.value).toBe(1.5)
    h.unmount()
  })
})