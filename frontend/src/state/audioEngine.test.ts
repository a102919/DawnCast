// audioEngine 單元測試：框架無關，直接呼叫 createAudioEngine()。
// happy-dom 缺少 HTMLMediaElement 行為，Audio 全部以 vi.fn 取代並在測試內手動推事件。

import { describe, expect, it, vi } from 'vitest'
import { createAudioEngine, type AudioEngine } from './audioEngine'

interface FakeAudioEl {
  src: string
  preload: string
  defaultPlaybackRate: number
  playbackRate: number
  volume: number
  muted: boolean
  paused: boolean
  readyState: number
  currentTime: number
  listeners: Map<string, Set<(e: Event) => void>>
  onended: ((e: Event) => void) | null
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
    preload: '',
    defaultPlaybackRate: 1,
    playbackRate: 1,
    volume: 1,
    muted: false,
    paused: true,
    readyState: 0,
    currentTime: 0,
    listeners,
    play: vi.fn(async () => {
      el.paused = false
      return undefined
    }),
    pause: vi.fn(() => { el.paused = true }),
    onended: null,
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
  // 模擬瀏覽器設 src 後觸發載入事件；「走 error 路徑」測試可以在設 src 之前
  // 用 (e as { _autoComplete?: boolean })._autoComplete = false 關掉自動派送。
  Object.defineProperty(el, 'src', {
    set(v: string) {
      ;(this as { _src: string; _autoComplete?: boolean })._src = v
      if (this._autoComplete === false) return
      this.readyState = 3
      this.dispatch('loadedmetadata')
      this.dispatch('loadeddata')
      this.dispatch('canplay')
    },
    get() { return (this as { _src: string })._src ?? '' },
  })
  return el
}

function setupGlobalMock() {
  const els: FakeAudioEl[] = []
  const Audio = vi.fn(() => {
    const e = makeFakeAudio()
    els.push(e)
    return e
  })
  ;(window as unknown as { Audio: unknown }).Audio = Audio
  return { els, Audio }
}

const HAVE_FUTURE_DATA = 3
const HAVE_METADATA = 1

describe('audioEngine', () => {
  it('attachToDOM 建隱形 host div 並 append 進 body（fake Audio 走 detached 降級；實機 audio 是真 Element 會進 host）', () => {
    setupGlobalMock()
    const body = document.body
    const appendChildSpy = vi.spyOn(body, 'appendChild')
    const createElSpy = vi.spyOn(document, 'createElement')
    createAudioEngine()
    expect(createElSpy).toHaveBeenCalledWith('div')
    expect(appendChildSpy).toHaveBeenCalled()
    // host 應帶 aria-hidden 與資料標記（給 cleanup dedup 用）
    const host = appendChildSpy.mock.calls[0]![0] as HTMLElement
    expect(host.getAttribute('aria-hidden')).toBe('true')
    expect(host.getAttribute('data-audio-host')).not.toBeNull()
  })

  it('unlock 在三個元素上各 play+pause 一次（iOS 同步授權）', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    engine.unlock()
    expect(els).toHaveLength(3)
    // pause 在 play() promise 的 .then() 內才觸發（避免 sync pause race 撤銷授權），
    // 等 microtask flush 後才能看到 pause 已被呼叫。
    await Promise.resolve()
    for (const el of els) {
      expect(el.play).toHaveBeenCalled()
      expect(el.pause).toHaveBeenCalled()
    }
  })

  it('preload：src 設進元素並等 canplay 時 resolve true', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    const p = engine.preload('https://cdn/0.mp3')
    expect(els[0]!.src).toContain('https://cdn/0.mp3')
    els[0]!.readyState = HAVE_FUTURE_DATA
    els[0]!.dispatch('canplay')
    await expect(p).resolves.toBe(true)
  })

  it('preload：元素 error 時 resolve false', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    ;(els[0] as { _autoComplete?: boolean })._autoComplete = false
    const p = engine.preload('https://cdn/bad.mp3')
    els[0]!.dispatch('error')
    await expect(p).resolves.toBe(false)
  })

  it('preload：已就緒元素直接 resolve，不重發事件', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    const el = els[0]!
    el.src = 'https://cdn/0.mp3'
    el.readyState = HAVE_FUTURE_DATA
    await expect(engine.preload('https://cdn/0.mp3')).resolves.toBe(true)
  })

  it('startPlayback：主播放設 src/currentTime/playbackRate 並 play', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    await engine.preload('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 5, offsetSec: 0.2, rate: 1.5 }, vi.fn(), vi.fn())
    expect(handle).not.toBeNull()
    const el = els[0]!
    expect(el.currentTime).toBe(0.2)
    expect(el.playbackRate).toBe(1.5)
    expect(el.play).toHaveBeenCalled()
  })

  it('startPlayback：試聽走第三個專用元素，且不影響主播放 src', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    await engine.preload('https://cdn/0.mp3')
    const mainSrcBefore = els[0]!.src
    const preview = engine.startPlayback(
      { url: 'https://cdn/0.mp3', globalStartSec: 0, offsetSec: 0, durationSec: 0.5, rate: 1 },
      vi.fn(), vi.fn(),
    )
    expect(preview.el).toBe(els[2])
    expect(els[0]!.src).toBe(mainSrcBefore)
  })

  it('stop：回傳位置含 offset 並 pause 元素', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    await engine.preload('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 10, offsetSec: 0.7, rate: 1 }, vi.fn(), vi.fn())!
    els[0]!.currentTime = 0.9
    expect(engine.stop(handle)).toBeCloseTo(10.9)
    expect(els[0]!.pause).toHaveBeenCalled()
  })

  it('currentPositionSec：metadata 未到時退回 offset，不會讓位置倒退', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    await engine.preload('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 100, offsetSec: 0.5, rate: 1 }, vi.fn(), vi.fn())!
    // metadata 未到時 currentTime 在某些實作會被瀏覽器吞掉，這裡手動先回到 0 模擬
    els[0]!.currentTime = 0
    expect(engine.currentPositionSec(handle)).toBeCloseTo(100.5)
  })

  it('setRate：同步更新 playbackRate', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    await engine.preload('https://cdn/0.mp3')
    const handle = engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 0, offsetSec: 0, rate: 1 }, vi.fn(), vi.fn())!
    engine.setRate(handle, 1.8)
    expect(els[0]!.playbackRate).toBe(1.8)
  })

  it('setMuted：三個元素 muted 一起被設', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    engine.setMuted(true)
    for (const el of els) expect(el.muted).toBe(true)
    engine.setMuted(false)
    for (const el of els) expect(el.muted).toBe(false)
  })

  it('onended 觸發時呼叫 callback', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    await engine.preload('https://cdn/0.mp3')
    const onEnded = vi.fn()
    engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 0, offsetSec: 0, rate: 1 }, onEnded, vi.fn())
    els[0]!.dispatch('ended')
    expect(onEnded).toHaveBeenCalled()
  })

  it('元素被新播放接手後，舊 handle 的 onended 觸發不再導回 onEnded callback（token identity guard）', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    await engine.preload('https://cdn/0.mp3')
    const oldEnded = vi.fn()
    engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 0, offsetSec: 0, rate: 1 }, oldEnded, vi.fn())
    // 同一個 element 第二次接播放會走「已經有這 URL」分支，主動覆寫 token。
    engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 0, offsetSec: 0, rate: 1 }, vi.fn(), vi.fn())
    // 舊 onEnded 還掛在 mainA，但 token 已被新播放覆寫，晚到的 ended 不該呼叫 oldEnded。
    els[0]!.dispatch('ended')
    expect(oldEnded).not.toHaveBeenCalled()
  })

  it('metadata 未到時設 currentTime，loadedmetadata 後再補一次（防止部分瀏覽器吞掉）', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine()
    ;(els[0] as { _autoComplete?: boolean })._autoComplete = false
    engine.startPlayback({ url: 'https://cdn/0.mp3', globalStartSec: 0, offsetSec: 0.3, rate: 1 }, vi.fn(), vi.fn())
    // 模擬 metadata 未到，currentTime 被瀏覽器吞掉
    els[0]!.currentTime = 0
    els[0]!.readyState = HAVE_METADATA
    els[0]!.dispatch('loadedmetadata')
    expect(els[0]!.currentTime).toBe(0.3)
  })

  it('primary API surface: AudioEngine 出口型別一致', () => {
    const engine: AudioEngine = createAudioEngine()
    expect(typeof engine.unlock).toBe('function')
    expect(typeof engine.preload).toBe('function')
    expect(typeof engine.startPlayback).toBe('function')
    expect(typeof engine.stop).toBe('function')
    expect(typeof engine.setRate).toBe('function')
    expect(typeof engine.currentPositionSec).toBe('function')
    expect(typeof engine.setMuted).toBe('function')
  })
})
