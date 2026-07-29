/** Web Audio 引擎：AudioContext 生命週期、LRU buffer cache、decode、排程播放、ducking。
 *
 * 框架無關（不 import React），不知道 episode/播放意圖這些概念——cache/decode 去重 key
 * 是 audioUrl 字串（不是 segment idx，跨集數天生不會撞名：舊集數某個 idx 的 decode 卡住
 * 遲到完成，只會寫回它自己那個 URL 的 cache entry，不會汙染新集數同一個 idx 的內容）。
 * 「同一時刻只播一個」是呼叫端的政策，引擎本身允許同時存在多個 PlaybackHandle。
 *
 * decode 本身不接受呼叫端的 AbortSignal——ctx.decodeAudioData 沒有原生 abort 支援，且
 * 同一個 URL 的 in-flight decode 可能被多個呼叫者共用（cache miss 時大家排隊等同一個
 * Promise），中止會連坐波及還沒過期的其他等待者。要不要對 decode 結果採取行動，交給
 * 呼叫端自己在拿到結果後判斷。
 *
 * iOS Safari：純 Web Audio API 輸出不會被系統認成合法的背景播放 session，因此輸出改接
 * 一個真的在播放的隱藏 <audio> 元素（透過 MediaStreamAudioDestinationNode）。
 */

const MAX_BUFFER_CACHE = 8
const DUCK_RAMP_SEC = 0.05

export interface PlaybackHandle {
  readonly source: AudioBufferSourceNode
  readonly ctxAnchorSec: number
  /** ctxAnchorSec 那一瞬間對應的**全域**播放秒數，已含段內 offset（＝globalStartSec + offsetSec）。
   *  currentPositionSec = anchorPositionSec + (ctx.currentTime - ctxAnchorSec) * rate。
   *  刻意不叫 globalStartSec：那個名字會讓人以為填 seg.start 就好，漏掉 offset 後
   *  seek 過的位置一律少報 offsetSec，連鎖弄壞進度條、pausedAt 與單句循環。 */
  readonly anchorPositionSec: number
}

export interface StartPlaybackArgs {
  readonly url: string
  readonly globalStartSec: number
  readonly offsetSec: number
  readonly durationSec?: number
  readonly rate: number
}

export interface AudioEngine {
  ensureContext(): AudioContext
  unlock(): Promise<void>
  getBuffer(url: string): Promise<AudioBuffer | null>
  hasBuffer(url: string): boolean
  clearCache(): void
  /** buffer 未 cache（尚未 getBuffer 完成）時回傳 null，呼叫端自行決定要不要重試。 */
  startPlayback(args: StartPlaybackArgs): PlaybackHandle | null
  /** 回傳停止當下算出的全域播放位置（秒），呼叫端拿去存 pausedAt。 */
  stop(handle: PlaybackHandle, rate: number): number
  setRate(handle: PlaybackHandle, rate: number): void
  currentPositionSec(handle: PlaybackHandle, rate: number): number
  setVolume(v: number): void
  duckDown(toLevel: number, rampSec: number): void
  restoreVolume(rampSec: number): void
}

/** LRU AudioBuffer cache，超過上限 evict 最舊。Map 保留 insertion order，touch = delete+set。 */
function createBufferCache() {
  const map = new Map<string, AudioBuffer>()
  const get = (url: string): AudioBuffer | undefined => {
    const b = map.get(url)
    if (b) { map.delete(url); map.set(url, b) }
    return b
  }
  const set = (url: string, b: AudioBuffer): void => {
    if (map.has(url)) map.delete(url)
    map.set(url, b)
    while (map.size > MAX_BUFFER_CACHE) {
      const oldest = map.keys().next().value
      if (oldest === undefined) break
      map.delete(oldest)
    }
  }
  const has = (url: string): boolean => map.has(url)
  const clear = (): void => map.clear()
  return { get, set, has, clear }
}

export function createAudioEngine(): AudioEngine {
  const ctxRef: { current: AudioContext | null } = { current: null }
  const mainGainRef: { current: GainNode | null } = { current: null }
  const segmentGainRef: { current: GainNode | null } = { current: null }
  const audioElRef: { current: HTMLAudioElement | null } = { current: null }
  const cache = createBufferCache()
  /** in-flight decode 用共用 Promise 去重，key 是 audioUrl；一個呼叫者放棄不影響其他
   *  還在等同一個 URL 的呼叫者（見檔案頂端說明）。 */
  const inflight = new Map<string, Promise<AudioBuffer | null>>()

  function ensureContext(): AudioContext {
    if (ctxRef.current) return ctxRef.current
    const Ctor = window.AudioContext
      ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = new Ctor()
    const mainGain = ctx.createGain()
    const segGain = ctx.createGain()
    segGain.gain.value = 1
    mainGain.connect(segGain)
    ctxRef.current = ctx
    mainGainRef.current = mainGain
    segmentGainRef.current = segGain

    // 輸出不接 ctx.destination，改接一個真的在播放的隱藏 <audio> 元素（透過
    // MediaStreamAudioDestinationNode）。iOS Safari 對純 Web Audio API 輸出不會
    // 認成合法的背景播放 session：光設 mediaSession metadata 沒用，沒有實際在播的
    // <audio>/<video> 元素，動態島/鎖屏不會顯示，背景切走也會被系統直接掛起 AudioContext。
    const streamDest = ctx.createMediaStreamDestination()
    segGain.connect(streamDest)
    const audioEl = new Audio()
    audioEl.srcObject = streamDest.stream
    audioEl.play().catch(() => undefined)
    audioElRef.current = audioEl

    return ctx
  }

  async function unlock(): Promise<void> {
    const ctx = ensureContext()
    void audioElRef.current?.play().catch(() => undefined)
    if (ctx.state === 'suspended') await ctx.resume()
  }

  async function getBuffer(url: string): Promise<AudioBuffer | null> {
    const cached = cache.get(url)
    if (cached) return cached
    let p = inflight.get(url)
    if (!p) {
      p = (async () => {
        try {
          const ctx = ensureContext()
          const res = await fetch(url)
          if (!res.ok) return null
          const buf = await ctx.decodeAudioData(await res.arrayBuffer())
          cache.set(url, buf)
          return buf
        } catch (e) {
          console.error('[audioEngine] decode failed', e)
          return null
        } finally {
          inflight.delete(url)
        }
      })()
      inflight.set(url, p)
    }
    return p
  }

  function startPlayback(args: StartPlaybackArgs): PlaybackHandle | null {
    const ctx = ctxRef.current
    const mainGain = mainGainRef.current
    const buf = cache.get(args.url)
    if (!ctx || !mainGain || !buf) return null

    const source = ctx.createBufferSource()
    source.buffer = buf
    source.playbackRate.value = args.rate
    source.connect(mainGain)
    void ctx.resume()
    // 背景切回來/鎖屏按播放時，隱藏 <audio> 可能已被系統暫停，這裡跟著重啟。
    void audioElRef.current?.play().catch(() => undefined)
    const remaining = args.durationSec !== undefined ? Math.min(args.durationSec, buf.duration - args.offsetSec) : undefined
    source.start(0, args.offsetSec, remaining)
    // 錨點位置＝段起點 + 段內 offset：source 是從 buffer 的 offsetSec 處開始播的，
    // 少加這一項就等於宣稱「不管從哪裡起播，位置都從段頭開始算」。
    return { source, ctxAnchorSec: ctx.currentTime, anchorPositionSec: args.globalStartSec + args.offsetSec }
  }

  function stop(handle: PlaybackHandle, rate: number): number {
    const ctx = ctxRef.current
    const pos = ctx ? handle.anchorPositionSec + (ctx.currentTime - handle.ctxAnchorSec) * rate : handle.anchorPositionSec
    try { handle.source.stop() } catch { /* already stopped */ }
    handle.source.disconnect()
    return pos
  }

  function setRate(handle: PlaybackHandle, rate: number): void {
    handle.source.playbackRate.value = rate
  }

  function currentPositionSec(handle: PlaybackHandle, rate: number): number {
    const ctx = ctxRef.current
    if (!ctx) return handle.anchorPositionSec
    return handle.anchorPositionSec + (ctx.currentTime - handle.ctxAnchorSec) * rate
  }

  function setVolume(v: number): void {
    const sg = segmentGainRef.current
    const ctx = ctxRef.current
    if (sg && ctx) sg.gain.setValueAtTime(v, ctx.currentTime)
  }

  function duckDown(toLevel: number, rampSec: number): void {
    const ctx = ctxRef.current
    const mainGain = mainGainRef.current
    if (!ctx || !mainGain) return
    const now = ctx.currentTime
    mainGain.gain.cancelScheduledValues(now)
    mainGain.gain.setValueAtTime(mainGain.gain.value, now)
    mainGain.gain.linearRampToValueAtTime(toLevel, now + rampSec)
  }

  function restoreVolume(rampSec: number): void {
    const ctx = ctxRef.current
    const mainGain = mainGainRef.current
    if (!ctx || !mainGain) return
    const t = ctx.currentTime
    mainGain.gain.cancelScheduledValues(t)
    mainGain.gain.setValueAtTime(mainGain.gain.value, t)
    mainGain.gain.linearRampToValueAtTime(1, t + rampSec)
  }

  return {
    ensureContext, unlock, getBuffer,
    hasBuffer: cache.has, clearCache: cache.clear,
    startPlayback, stop, setRate, currentPositionSec, setVolume, duckDown, restoreVolume,
  }
}

export { DUCK_RAMP_SEC }
