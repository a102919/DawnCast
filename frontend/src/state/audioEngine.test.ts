// audioEngine 單元測試：框架無關，直接呼叫 createAudioEngine()。
// happy-dom 缺少 HTMLMediaElement 行為，Audio 全部以 vi.fn 取代並在測試內手動推事件。

import { describe, expect, it, vi } from 'vitest'
import { createAudioEngine, type AudioEngine, type MainEventHandlers } from './audioEngine'

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
  duration: number
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
    preload: '',
    defaultPlaybackRate: 1,
    playbackRate: 1,
    volume: 1,
    muted: false,
    paused: true,
    readyState: 0,
    currentTime: 0,
    duration: NaN,
    listeners,
    play: vi.fn(async () => {
      el.paused = false
      el.dispatch('play')
      return undefined
    }),
    pause: vi.fn(() => { el.paused = true; el.dispatch('pause') }),
    addEventListener: vi.fn((name: string, cb: (e: Event) => void) => {
      if (!listeners.has(name)) listeners.set(name, new Set())
      listeners.get(name)!.add(cb)
    }),
    removeEventListener: vi.fn((name: string, cb: (e: Event) => void) => {
      listeners.get(name)?.delete(cb)
    }),
    dispatch(name: string, e: Event = new Event(name)) {
      // fake addEventListener 不理會 { once: true }（第三參數整個被忽略），
      // 呼叫端（各測試案）自己保證每個 once 事件只 dispatch 一次，跟原測試檔同慣例。
      for (const cb of Array.from(listeners.get(name) ?? [])) cb(e)
    },
  }
  // 模擬瀏覽器設 src 後觸發載入事件；「走 metadata 未到」測試可以在設 src 之前
  // 用 (e as { _autoComplete?: boolean })._autoComplete = false 關掉自動派送。
  Object.defineProperty(el, 'src', {
    set(v: string) {
      ;(this as { _src: string; _autoComplete?: boolean })._src = v
      if (this._autoComplete === false) return
      this.readyState = 3
      this.duration = 100
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

function makeHandlers(overrides: Partial<MainEventHandlers> = {}): MainEventHandlers {
  return {
    onTimeUpdate: vi.fn(),
    onSeeked: vi.fn(),
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onEnded: vi.fn(),
    ...overrides,
  }
}

const HAVE_METADATA = 1
const HAVE_FUTURE_DATA = 3

describe('audioEngine', () => {
  it('attachToDOM 建隱形 host div 並 append 進 body（fake Audio 走 detached 降級；實機 audio 是真 Element 會進 host）', () => {
    setupGlobalMock()
    const body = document.body
    const appendChildSpy = vi.spyOn(body, 'appendChild')
    const createElSpy = vi.spyOn(document, 'createElement')
    createAudioEngine(makeHandlers())
    expect(createElSpy).toHaveBeenCalledWith('div')
    expect(appendChildSpy).toHaveBeenCalled()
    const host = appendChildSpy.mock.calls[0]![0] as HTMLElement
    expect(host.getAttribute('aria-hidden')).toBe('true')
    expect(host.getAttribute('data-audio-host')).not.toBeNull()
  })

  it('load：mainEl 與 previewEl 都載同一個 episode URL（試聽是對整集檔任意區段抽樣）', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.load('https://cdn/episode.mp3')
    expect(els[0]!.src).toBe('https://cdn/episode.mp3')
    expect(els[1]!.src).toBe('https://cdn/episode.mp3')
  })

  it('load：換集時停掉還在放的舊集試聽（pause previewEl）', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.load('https://cdn/ep-1.mp3')
    engine.playClip(2, 5)
    const preview = els[1]!
    const pauseBefore = preview.pause.mock.calls.length
    engine.load('https://cdn/ep-2.mp3')
    expect(preview.pause.mock.calls.length).toBeGreaterThan(pauseBefore)
    expect(preview.src).toBe('https://cdn/ep-2.mp3')
  })

  it('play：呼叫 mainEl.play()，成功後觸發 onPlay handler', async () => {
    const { els } = setupGlobalMock()
    const handlers = makeHandlers()
    const engine = createAudioEngine(handlers)
    engine.load('https://cdn/episode.mp3')
    await engine.play()
    expect(els[0]!.play).toHaveBeenCalledTimes(1)
    expect(handlers.onPlay).toHaveBeenCalledTimes(1)
  })

  it('pause：play() promise 未 resolve 前呼叫，要等 promise 落定才真的 pause（AbortError race guard）', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.load('https://cdn/episode.mp3')
    void engine.play()
    engine.pause()
    // play() 的 promise 是 async 函式回傳，還沒 microtask flush 前不該看到 pause 被呼叫。
    expect(els[0]!.pause).not.toHaveBeenCalled()
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    expect(els[0]!.pause).toHaveBeenCalledTimes(1)
  })

  it('pause：沒有 pending play 時直接同步 pause', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.load('https://cdn/episode.mp3')
    engine.pause()
    expect(els[0]!.pause).toHaveBeenCalledTimes(1)
  })

  it('seek：metadata 已到（readyState>=1）直接設 currentTime，不註冊 listener', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.load('https://cdn/episode.mp3') // fake src setter 自動把 readyState 推到 3
    engine.seek(12.5)
    expect(els[0]!.currentTime).toBe(12.5)
  })

  it('seek：metadata 未到時設 currentTime 可能被瀏覽器吞掉，loadedmetadata 後補設一次', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    const mainEl = els[0]!
    ;(mainEl as unknown as { _autoComplete?: boolean })._autoComplete = false
    engine.load('https://cdn/episode.mp3')
    engine.seek(30)
    expect(mainEl.currentTime).toBe(30)
    // 模擬瀏覽器把剛剛的賦值吞掉
    mainEl.currentTime = 0
    mainEl.readyState = HAVE_METADATA
    mainEl.dispatch('loadedmetadata')
    expect(mainEl.currentTime).toBe(30)
  })

  it('切集重置：load() 換新集數後，舊 generation 的補設 seek 不再執行', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    const mainEl = els[0]!
    ;(mainEl as unknown as { _autoComplete?: boolean })._autoComplete = false
    engine.load('https://cdn/ep-1.mp3')
    engine.seek(50) // metadata 未到，註冊 generation=1 的補設 listener

    engine.load('https://cdn/ep-2.mp3') // generation 前進到 2；模擬瀏覽器換 src 後歸零位置
    mainEl.currentTime = 0

    // ep-1 的 loadedmetadata 姍姍來遲（fake 環境手動觸發模擬）：generation 已過期，不該把
    // 位置改回 50，否則使用者聽到的會是「跳回上一集的續播位置」。
    mainEl.dispatch('loadedmetadata')
    expect(mainEl.currentTime).toBe(0)
  })

  it('ended：mainEl 觸發 ended 事件呼叫 onEnded handler', () => {
    const { els } = setupGlobalMock()
    const handlers = makeHandlers()
    createAudioEngine(handlers)
    els[0]!.dispatch('ended')
    expect(handlers.onEnded).toHaveBeenCalledTimes(1)
  })

  it('timeupdate/seeked：mainEl 觸發時呼叫對應 handler，currentTime()/duration() 讀到最新值', () => {
    const { els } = setupGlobalMock()
    const handlers = makeHandlers()
    const engine = createAudioEngine(handlers)
    engine.load('https://cdn/episode.mp3')
    els[0]!.currentTime = 42
    els[0]!.dispatch('timeupdate')
    expect(handlers.onTimeUpdate).toHaveBeenCalledTimes(1)
    expect(engine.currentTime()).toBe(42)
    expect(engine.duration()).toBe(100)

    els[0]!.currentTime = 10
    els[0]!.dispatch('seeked')
    expect(handlers.onSeeked).toHaveBeenCalledTimes(1)
    expect(engine.currentTime()).toBe(10)
  })

  it('尚未 load 前：duration() 回傳 NaN、currentTime() 回傳 0（原生 <audio> 預設值）', () => {
    setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    expect(engine.currentTime()).toBe(0)
    expect(Number.isNaN(engine.duration())).toBe(true)
  })

  it('setRate：mainEl 與 previewEl 的 playbackRate 一起被設', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.setRate(1.5)
    expect(els[0]!.playbackRate).toBe(1.5)
    expect(els[0]!.defaultPlaybackRate).toBe(1.5)
    expect(els[1]!.playbackRate).toBe(1.5)
    expect(els[1]!.defaultPlaybackRate).toBe(1.5)
  })

  it('setMuted：mainEl 與 previewEl 一起被設，音量（volume）不受影響', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.setMuted(true)
    expect(els[0]!.muted).toBe(true)
    expect(els[1]!.muted).toBe(true)
    engine.setMuted(false)
    expect(els[0]!.muted).toBe(false)
    expect(els[1]!.muted).toBe(false)
  })

  it('playClip：previewEl seek 到 startSec 並播放，playing 事件後起算 durationSec 限長自動停止', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    const preview = els[1]!
    preview.readyState = HAVE_FUTURE_DATA
    engine.playClip(2, 0.05)
    expect(preview.currentTime).toBe(2)
    expect(preview.play).toHaveBeenCalledTimes(1)
    // playClip 一開始會先 pause 掉上一次殘留的試聽播放（cancel-previous），這次呼叫本身
    // 就會讓 pause 計數 +1；限長停止是「額外再呼叫一次」，用 baseline 比對才準確。
    const pauseCallsBeforePlaying = preview.pause.mock.calls.length

    preview.dispatch('playing')
    expect(preview.pause.mock.calls.length).toBe(pauseCallsBeforePlaying)
    await new Promise((r) => setTimeout(r, 80))
    expect(preview.pause.mock.calls.length).toBe(pauseCallsBeforePlaying + 1)
  })

  it('playClip：前一次試聽還沒進 playing 就被取代時，舊 listener 被拆掉，不會用舊 stopAt 提早停掉新試聽', async () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    const preview = els[1]!
    preview.readyState = HAVE_FUTURE_DATA
    engine.playClip(2, 0.01) // 短 clip，從未觸發 playing
    engine.playClip(10, 5) // 立刻被長 clip 取代
    preview.currentTime = 10
    const pauseBaseline = preview.pause.mock.calls.length
    preview.dispatch('playing') // 只有新 listener 該開火：remainMs = 5s / rate
    await new Promise((r) => setTimeout(r, 50))
    // 舊 listener 若殘留，會算出 remainMs ≈ 0 立刻 pause；新 listener 的 5s 定時還沒到。
    expect(preview.pause.mock.calls.length).toBe(pauseBaseline)
  })

  it('playClip：不影響主播放元素的 src / currentTime', () => {
    const { els } = setupGlobalMock()
    const engine = createAudioEngine(makeHandlers())
    engine.load('https://cdn/episode.mp3')
    engine.seek(20)
    const mainSrcBefore = els[0]!.src
    const mainTimeBefore = els[0]!.currentTime
    engine.playClip(5, 0.3)
    expect(els[0]!.src).toBe(mainSrcBefore)
    expect(els[0]!.currentTime).toBe(mainTimeBefore)
  })

  it('primary API surface: AudioEngine 出口型別一致', () => {
    setupGlobalMock()
    const engine: AudioEngine = createAudioEngine(makeHandlers())
    expect(typeof engine.load).toBe('function')
    expect(typeof engine.play).toBe('function')
    expect(typeof engine.pause).toBe('function')
    expect(typeof engine.seek).toBe('function')
    expect(typeof engine.setRate).toBe('function')
    expect(typeof engine.setMuted).toBe('function')
    expect(typeof engine.currentTime).toBe('function')
    expect(typeof engine.duration).toBe('function')
    expect(typeof engine.playClip).toBe('function')
  })
})
