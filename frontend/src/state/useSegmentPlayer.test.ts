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

// happy-dom 沒有 AudioWorkletNode，見 audioEngine.test.ts 同一段註解。
vi.mock('@soundtouchjs/audio-worklet/processor?url', () => ({ default: 'mock-processor-url' }))
vi.mock('@soundtouchjs/audio-worklet', () => ({
  SoundTouchNode: class {
    playbackRate = { value: 1 }
    connect = vi.fn()
    static register = vi.fn(async () => undefined)
  },
}))

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
  createMediaStreamDestination: ReturnType<typeof vi.fn>
  resume: ReturnType<typeof vi.fn>
  suspend: ReturnType<typeof vi.fn>
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
    createMediaStreamDestination: vi.fn(() => ({ stream: {}, connect: vi.fn() })),
    resume: vi.fn(async () => undefined),
    suspend: vi.fn(async () => undefined),
  }
  ;(window as unknown as { AudioContext: unknown }).AudioContext = vi.fn(() => fakeCtx)
  ;(window as unknown as { Audio: unknown }).Audio = vi.fn(() => ({
    srcObject: null,
    play: vi.fn(async () => undefined),
    pause: vi.fn(),
  }))
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

  it('playSegment（單字/片語試聽）不動全域 isPlaying，播完也不會自動接播下一段', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3))
    })
    expect(h.getPlayer().isPlaying).toBe(false)

    await act(async () => {
      h.getPlayer().playSegment(0, 0, 0.5)
      await new Promise((r) => setTimeout(r, 10))
    })
    expect(h.getPlayer().isPlaying).toBe(false)
    expect(sources.length).toBe(1)

    // 試聽片段自然播完：不能像整集播放一樣自動接播下一段
    act(() => {
      sources[0]!.onended!(null)
    })
    expect(sources.length).toBe(1)
    expect(h.getPlayer().isPlaying).toBe(false)
    h.unmount()
  })

  it('playSegment 不動主播放游標：試聽別段後續播，仍從原本暫停的位置接續', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3))
    })
    // 在第 0 段播到 0.4 秒後暫停
    await act(async () => {
      await h.getPlayer().play()
    })
    fakeCtx.currentTime = 0.4
    act(() => {
      h.getPlayer().pause()
    })

    // 字卡發音：試聽第 2 段（全域起點 2.2）的一小段
    await act(async () => {
      h.getPlayer().playSegment(2, 0.3, 0.5)
      await new Promise((r) => setTimeout(r, 10))
    })
    fakeCtx.currentTime = 0.6
    act(() => {
      sources.at(-1)!.onended!(null) // 試聽自然播完
    })

    // 續播：要回到第 0 段的 0.4 秒。若游標被試聽帶走（segIdx=2、pausedAt=試聽結束位置），
    // 這裡會變成從第 2 段的 0.5 秒起播。第三個參數 undefined 代表這是主播放（播到段尾），
    // 不是又一次限定長度的試聽。
    await act(async () => {
      await h.getPlayer().play()
    })
    expect(sources.at(-1)!.start).toHaveBeenCalledWith(0, expect.closeTo(0.4), undefined)
    h.unmount()
  })

  it('duck 音量還沒回滿時使用者開始播放，立刻恢復滿音量，不等原訂的 duck 結束時間', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(2))
    })
    const mainGain = gains[0]!
    await act(async () => {
      h.getPlayer().playSegment(0, 0, 2.0) // duck 排定 2 秒後才回滿音量
      await new Promise((r) => setTimeout(r, 10))
    })
    mainGain.gain.linearRampToValueAtTime.mockClear()

    // 還在 duck 音量期間（遠早於原訂的 2000ms）就開始播放整集
    await act(async () => {
      await h.getPlayer().play()
    })

    expect(mainGain.gain.linearRampToValueAtTime).toHaveBeenCalledWith(1, expect.any(Number))
    h.unmount()
  })

  it('loadEpisode 過期的呼叫不能覆寫較新那次的 loadState，也不能吃掉較新那次的播放意圖', async () => {
    const h = mountHook()
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    let resolveB: (() => void) | null = null
    let resolveC: (() => void) | null = null
    fetchMock.mockImplementation(async (url: unknown) => {
      const u = String(url)
      if (u.includes('https://b/')) await new Promise<void>((r) => { resolveB = r })
      if (u.includes('https://c/')) await new Promise<void>((r) => { resolveC = r })
      return { ok: true, arrayBuffer: async () => new Uint8Array([0x00]).buffer }
    })

    // 連續切兩次集數（例如連點兩下「下一集」）：B 還沒載完，C 就蓋上去了
    let pB: Promise<void> = Promise.resolve()
    let pC: Promise<void> = Promise.resolve()
    act(() => {
      pB = h.getPlayer().loadEpisode(makeEpisode(2, 'https://b/'))
    })
    act(() => {
      pC = h.getPlayer().loadEpisode(makeEpisode(2, 'https://c/'))
    })

    // C 還在 loading 時使用者按播放
    await act(async () => {
      await h.getPlayer().play()
    })
    expect(h.getPlayer().loadState).toBe('loading')

    // 讓過期的 B 先完成——不能把 loadState 蓋成 ready，也不能把上面設下的播放意圖吃掉清空
    await act(async () => {
      resolveB?.()
      await pB
    })
    expect(h.getPlayer().loadState).toBe('loading')

    // C 才是目前這集，讓它完成，播放意圖要能正確被消費
    await act(async () => {
      resolveC?.()
      await pC
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(h.getPlayer().loadState).toBe('ready')
    expect(h.getPlayer().isPlaying).toBe(true)
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

  it('pause 後 play 從暫停位置接續，不是從段落開頭重播', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(2))
    })
    await act(async () => {
      await h.getPlayer().play()
    })
    expect(sources.at(-1)!.start).toHaveBeenCalledWith(0, 0, undefined)

    fakeCtx.currentTime = 0.4 // 播了 0.4 秒
    act(() => {
      h.getPlayer().pause()
    })
    // iOS 迴歸：pause 必須真的 suspend 輸出鏈，不能留著活的音訊 session（見 audioEngine.suspend）
    expect(fakeCtx.suspend).toHaveBeenCalled()
    await act(async () => {
      await h.getPlayer().play()
    })
    expect(sources.at(-1)!.start).toHaveBeenCalledWith(0, expect.closeTo(0.4), undefined)
    h.unmount()
  })

  it('自動接播段落卡在解碼時使用者 seek 到別處，解碼遲到完成後不能再疊一個 source（race regression）', async () => {
    const h = mountHook()
    const ep = makeEpisode(3)

    let resolveSeg1: (() => void) | null = null
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    fetchMock.mockImplementation(async (url: unknown) => {
      if (String(url).includes('/1.mp3')) {
        await new Promise<void>((resolve) => { resolveSeg1 = resolve })
      }
      return { ok: true, arrayBuffer: async () => new Uint8Array([0x00]).buffer }
    })

    await act(async () => {
      await h.getPlayer().loadEpisode(ep)
    })
    await act(async () => {
      await h.getPlayer().play()
    })
    expect(sources.length).toBe(1)

    // segment 0 自然播完，onended 觸發自動接播 segment 1；segment 1 的解碼被上面攔截卡住未完成
    act(() => {
      sources[0]!.onended!(null)
    })

    // 使用者在自動接播卡在解碼的當下 seek 回 segment 0 重播
    await act(async () => {
      h.getPlayer().seekTo(0)
      await new Promise((r) => setTimeout(r, 0))
    })
    expect(sources.length).toBe(2)

    // 卡住的 segment 1 解碼現在才姍姍來遲完成——這條自動接播鏈此時已經過期
    await act(async () => {
      resolveSeg1?.()
      await new Promise((r) => setTimeout(r, 120)) // 蓋過 DECODE_POLL_MS 輪詢間隔
    })

    // 過期的自動接播不能再疊一個 segment 1 的 source 上去，蓋掉使用者 seek 後正在播的 segment 0
    expect(sources.length).toBe(2)
    h.unmount()
  })

  it('自動接播段落之間有停頓：buffer 已就緒也不會馬上接播，等 gap 時間到才接（見 makeEpisode 的 0.1s 間隔）', async () => {
    const h = mountHook()
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3))
    })
    await act(async () => {
      await h.getPlayer().play()
    })
    expect(sources.length).toBe(1)

    await act(async () => {
      sources[0]!.onended!(null)
      await new Promise((r) => setTimeout(r, 0)) // 讓 ensureBuffer(next) 的 fetch/decode resolve
    })
    expect(sources.length).toBe(1) // gap 還沒到，不能馬上接播

    await act(async () => {
      await new Promise((r) => setTimeout(r, 150)) // 蓋過 100ms gap
    })
    expect(sources.length).toBe(2)
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

  it('切換集數時上一集卡住的 decode 遲到完成，不會讓新集數同一個 idx 誤判成 cache 命中而跳過真正的 fetch（跨集數 cache 汙染 regression）', async () => {
    const h = mountHook()
    let resolveStaleA1: (() => void) | null = null
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    fetchMock.mockImplementation(async (url: unknown) => {
      if (String(url) === 'https://a/1.mp3') {
        await new Promise<void>((resolve) => { resolveStaleA1 = resolve })
      }
      return { ok: true, arrayBuffer: async () => new Uint8Array([0x00]).buffer }
    })

    // 集數 A 載入，prefetch 順便觸發 idx=1（https://a/1.mp3）的 decode，卡住不放
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3, 'https://a/'))
    })

    // 切到集數 B：B 自己的 idx=1（https://b/1.mp3）必須真的被 fetch，
    // 不能因為舊版 idx-keyed cache 誤判命中而跳過
    await act(async () => {
      await h.getPlayer().loadEpisode(makeEpisode(3, 'https://b/'))
      await new Promise((r) => setTimeout(r, 0)) // 讓 prefetchAround 的 fire-and-forget fetch 有機會發出
    })
    const calledUrls = fetchMock.mock.calls.map((c) => String(c[0]))
    expect(calledUrls).toContain('https://b/1.mp3')

    // 舊集數卡住的 decode 這時候才姍姍來遲完成，不能拋錯或影響已經在跑的新集數
    await act(async () => {
      resolveStaleA1?.()
      await new Promise((r) => setTimeout(r, 0))
    })

    h.unmount()
  })
})