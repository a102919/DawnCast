// audioEngine 單元測試：框架無關，不需要 React/act/mountHook，直接呼叫 createAudioEngine()。
// 涵蓋 useSegmentPlayer.test.ts 沒有精確驗證到的引擎內部細節：decode 去重、LRU eviction、
// 跨 URL 不撞名（修掉舊版 idx-keyed cache 跨集數汙染的 bug）、AudioParam 排程順序。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createAudioEngine, type AudioEngine } from './audioEngine'

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

interface FakeAudioEl {
  srcObject: unknown
  play: ReturnType<typeof vi.fn>
  pause: ReturnType<typeof vi.fn>
}

const sources: FakeBufferSourceNode[] = []
const gains: FakeGainNode[] = []
const audioEls: FakeAudioEl[] = []
let fakeCtx: FakeAudioContext
let realFetch: typeof fetch

function setupGlobalMocks() {
  sources.length = 0
  gains.length = 0
  audioEls.length = 0
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
  ;(window as unknown as { Audio: unknown }).Audio = vi.fn(() => {
    const el: FakeAudioEl = { srcObject: null, play: vi.fn(async () => undefined), pause: vi.fn() }
    audioEls.push(el)
    return el
  })
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

describe('audioEngine', () => {
  let engine: AudioEngine

  beforeEach(() => {
    setupGlobalMocks()
    engine = createAudioEngine()
  })
  afterEach(() => {
    teardownGlobalMocks()
  })

  it('unlock：suspended 狀態才呼叫 ctx.resume', async () => {
    fakeCtx.state = 'suspended'
    await engine.unlock()
    expect(fakeCtx.resume).toHaveBeenCalled()
  })

  it('getBuffer：fetch + decodeAudioData，並 cache 結果', async () => {
    const buf = await engine.getBuffer('https://cdn/0.mp3')
    expect(buf).not.toBeNull()
    expect(global.fetch).toHaveBeenCalledWith('https://cdn/0.mp3')
    expect(fakeCtx.decodeAudioData).toHaveBeenCalledTimes(1)
    expect(engine.hasBuffer('https://cdn/0.mp3')).toBe(true)

    // 第二次呼叫命中 cache，不重新 fetch
    await engine.getBuffer('https://cdn/0.mp3')
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })

  it('getBuffer：同一個 URL 併發呼叫共用同一個 in-flight decode，只 fetch 一次', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    // 用物件持有 resolve callback（而非裸 let）：resolveFetch 的賦值發生在巢狀 Promise
    // executor 內，緊接著同步呼叫 resolveFetch?.()（中間沒有 await）會被 TS 窄化成 never
    // （見 tsc 2349）——裸 let 跨 closure 邊界的窄化分析沒辦法追蹤這種同步巢狀賦值，
    // 物件屬性不受這個限制。
    const holder: { resolve: (() => void) | null } = { resolve: null }
    fetchMock.mockImplementation(async () => {
      await new Promise<void>((resolve) => { holder.resolve = resolve })
      return { ok: true, arrayBuffer: async () => new Uint8Array([0x00]).buffer }
    })

    const p1 = engine.getBuffer('https://cdn/x.mp3')
    const p2 = engine.getBuffer('https://cdn/x.mp3')
    holder.resolve?.()
    const [b1, b2] = await Promise.all([p1, p2])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(b1).toBe(b2) // 同一個 AudioBuffer 物件參考
  })

  it('跨 URL 不撞名：一個 URL 的遲到 decode 不會汙染另一個 URL 的 cache（修掉舊版 idx-keyed 的跨集數汙染 bug）', async () => {
    const fetchMock = global.fetch as ReturnType<typeof vi.fn>
    const holder: { resolve: (() => void) | null } = { resolve: null }
    fetchMock.mockImplementation(async (url: unknown) => {
      if (String(url).includes('stale')) {
        await new Promise<void>((resolve) => { holder.resolve = resolve })
      }
      return { ok: true, arrayBuffer: async () => new Uint8Array([0x00]).buffer }
    })

    // 模擬：上一集某個 idx 的 decode 卡住（stale），使用者已經換到新集數，
    // 新集數同一個 idx 對應到不同的 audioUrl，正常解碼完成。
    const stalePromise = engine.getBuffer('https://a/stale-2.mp3')
    const freshBuf = await engine.getBuffer('https://b/fresh-2.mp3')
    expect(freshBuf).not.toBeNull()

    // 卡住的舊集數 decode 這時候才姍姍來遲完成
    holder.resolve?.()
    await stalePromise

    // 兩個 URL 各自有各自的 cache entry，互不影響
    expect(engine.hasBuffer('https://a/stale-2.mp3')).toBe(true)
    expect(engine.hasBuffer('https://b/fresh-2.mp3')).toBe(true)
    // 新集數的 buffer 沒有被舊集數的遲到結果覆蓋掉
    const freshAgain = await engine.getBuffer('https://b/fresh-2.mp3')
    expect(freshAgain).toBe(freshBuf)
  })

  it('LRU：超過 8 筆 evict 最舊的', async () => {
    for (let i = 0; i < 9; i++) {
      await engine.getBuffer(`https://cdn/${i}.mp3`)
    }
    expect(engine.hasBuffer('https://cdn/0.mp3')).toBe(false) // 最舊的被 evict
    expect(engine.hasBuffer('https://cdn/8.mp3')).toBe(true)
  })

  it('clearCache：清空後重新 getBuffer 會重新 fetch', async () => {
    await engine.getBuffer('https://cdn/0.mp3')
    engine.clearCache()
    expect(engine.hasBuffer('https://cdn/0.mp3')).toBe(false)
    await engine.getBuffer('https://cdn/0.mp3')
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it('startPlayback：buffer 未 cache 時回傳 null', () => {
    engine.ensureContext()
    const handle = engine.startPlayback({ url: 'https://cdn/never-fetched.mp3', globalStartSec: 0, offsetSec: 0, rate: 1 })
    expect(handle).toBeNull()
  })

  it('startPlayback：建立 source、connect、依 offset/duration 呼叫 start', async () => {
    await engine.getBuffer('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 5, offsetSec: 0.2, durationSec: 0.5, rate: 1.5 })
    expect(handle).not.toBeNull()
    const source = sources.at(-1)!
    expect(source.playbackRate.value).toBe(1.5)
    expect(source.connect).toHaveBeenCalled()
    expect(source.start).toHaveBeenCalledWith(0, 0.2, expect.any(Number))
  })

  it('stop：回傳算出的全域位置，並呼叫 source.stop + disconnect', async () => {
    await engine.getBuffer('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 10, offsetSec: 0, rate: 1 })!
    fakeCtx.currentTime = handle.ctxAnchorSec + 0.4
    const pos = engine.stop(handle, 1)
    expect(pos).toBeCloseTo(10.4)
    expect(handle.source.stop).toHaveBeenCalled()
    expect(handle.source.disconnect).toHaveBeenCalled()
  })

  it('currentPositionSec：錨點公式含 playbackRate', async () => {
    await engine.getBuffer('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 0, offsetSec: 0, rate: 1 })!
    fakeCtx.currentTime = handle.ctxAnchorSec + 1
    expect(engine.currentPositionSec(handle, 2)).toBeCloseTo(2)
  })

  // 迴歸：offsetSec 曾經沒被算進錨點，位置一律少報 offsetSec（seek 完位置退回段頭）。
  // 上面兩題 offsetSec 都是 0，兩種公式結果一樣，所以測不出來——這裡刻意用非 0 offset。
  it('currentPositionSec：從段內 offset 起播時位置含 offset，不會退回段頭', async () => {
    await engine.getBuffer('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 100, offsetSec: 0.7, rate: 1 })!
    expect(engine.currentPositionSec(handle, 1)).toBeCloseTo(100.7)
    fakeCtx.currentTime = handle.ctxAnchorSec + 0.2
    expect(engine.currentPositionSec(handle, 1)).toBeCloseTo(100.9)
  })

  it('stop：從段內 offset 起播時回傳位置含 offset（pausedAt 才不會倒退）', async () => {
    await engine.getBuffer('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 100, offsetSec: 0.7, rate: 1 })!
    fakeCtx.currentTime = handle.ctxAnchorSec + 0.2
    expect(engine.stop(handle, 1)).toBeCloseTo(100.9)
  })

  it('duckDown/restoreVolume：AudioParam 三連發順序（cancel → setValueAtTime → ramp）', () => {
    engine.ensureContext()
    const mainGain = gains[0]!
    engine.duckDown(0.3, 0.05)
    expect(mainGain.gain.cancelScheduledValues).toHaveBeenCalled()
    expect(mainGain.gain.setValueAtTime).toHaveBeenCalled()
    expect(mainGain.gain.linearRampToValueAtTime).toHaveBeenCalledWith(0.3, expect.any(Number))

    mainGain.gain.linearRampToValueAtTime.mockClear()
    engine.restoreVolume(0.05)
    expect(mainGain.gain.linearRampToValueAtTime).toHaveBeenCalledWith(1, expect.any(Number))
  })

  // 迴歸：iOS 暫停後若讓 ctx 照跑、隱藏 <audio> 繼續播 live MediaStream，系統音訊
  // session 保持開啟，殘留 buffer 可能被卡住無限重播（聽起來像一直發同一個音）。
  it('suspend：暫停隱藏 <audio> 並 suspend AudioContext', () => {
    engine.ensureContext()
    engine.suspend()
    expect(audioEls[0]?.pause).toHaveBeenCalled()
    expect(fakeCtx.suspend).toHaveBeenCalled()
  })

  it('suspend：context 尚未建立時是 no-op 不炸', () => {
    expect(() => engine.suspend()).not.toThrow()
  })

  it('setVolume：寫入 segmentGain（gains[1]）', () => {
    engine.ensureContext()
    const segmentGain = gains[1]!
    engine.setVolume(0.5)
    expect(segmentGain.gain.setValueAtTime).toHaveBeenCalledWith(0.5, expect.any(Number))
  })
})
