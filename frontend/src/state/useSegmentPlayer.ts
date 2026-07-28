/** Web Audio API 狀態機：lazy decode + LRU 8 段 + ducking + gesture unlock。
 *
 * 取代舊 <audio> 元素承載；後端每行 mp3 = 一個 segment，前端 fetch + decodeAudioData 後
 * 用 AudioBufferSourceNode 串接播（source 不能 reuse）。playbackRate 走 source.playbackRate.value；
 * currentTime = seg.start + (ctx.currentTime - ctxAnchor) * playbackRate。
 * 單字抽樣 playSegment 走 ducking：main gain ramp 50ms 降到 0.3，duration 結束 ramp 回 1.0。
 *
 * iOS Safari：首次 play() 必須 click handler 同步路徑內 ctx.resume()，否則 gesture 解鎖失效。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { Episode } from '../types/episode'

export type SegmentLoadState = 'idle' | 'loading' | 'ready' | 'error'

const MAX_BUFFER_CACHE = 8
const DUCK_RAMP_SEC = 0.05
const DUCK_LEVEL = 0.3
const DECODE_POLL_MS = 50

interface ActiveSource {
  readonly source: AudioBufferSourceNode
  readonly ctxAnchorSec: number
  /** 對應 cue.start；currentTime = globalStartSec + (ctx.currentTime - ctxAnchor) * rate */
  readonly globalStartSec: number
}

export interface SegmentPlayer {
  readonly loadState: SegmentLoadState
  readonly isPlaying: boolean
  readonly currentTime: number
  readonly duration: number
  readonly playbackRate: number
  readonly volume: number
  unlock(): Promise<void>
  loadEpisode(episode: Episode | null): Promise<void>
  play(): Promise<void>
  pause(): void
  seekTo(globalSec: number): void
  setPlaybackRate(rate: number): void
  setVolume(v: number): void
  playSegment(segmentIdx: number, offsetSec: number, durationSec: number): void
}

/** LRU AudioBuffer cache，超過上限 evict 最舊。Map 保留 insertion order，touch = delete+set。 */
function useBufferCache() {
  const ref = useRef<Map<number, AudioBuffer>>(new Map())
  const get = (idx: number) => {
    const m = ref.current
    const b = m.get(idx)
    if (b) { m.delete(idx); m.set(idx, b) }
    return b
  }
  const set = (idx: number, b: AudioBuffer) => {
    const m = ref.current
    if (m.has(idx)) m.delete(idx)
    m.set(idx, b)
    while (m.size > MAX_BUFFER_CACHE) {
      const oldest = m.keys().next().value
      if (oldest === undefined) break
      m.delete(oldest)
    }
  }
  const has = (idx: number) => ref.current.has(idx)
  const clear = () => ref.current.clear()
  return { get, set, has, clear }
}

export function useSegmentPlayer(): SegmentPlayer {
  const ctxRef = useRef<AudioContext | null>(null)
  const mainGainRef = useRef<GainNode | null>(null)
  const segmentGainRef = useRef<GainNode | null>(null)
  const cache = useBufferCache()
  const episodeRef = useRef<Episode | null>(null)
  const activeRef = useRef<ActiveSource | null>(null)
  const segIdxRef = useRef<number>(0)
  const isPlayingRef = useRef<boolean>(false)
  const rateRef = useRef<number>(1)
  const pendingRef = useRef<'play' | null>(null)
  const decodingRef = useRef<Set<number>>(new Set())
  /** 暫停/seek/換段當下的全域播放位置，play() 靠它算 resume 要從段內哪個 offset 接續，
   *  不能吃 currentTime state（rAF 節流，可能落後一幀）。 */
  const pausedAtRef = useRef<number>(0)

  const [loadState, setLoadState] = useState<SegmentLoadState>('idle')
  const [isPlaying, setIsPlaying] = useState<boolean>(false)
  const [currentTime, setCurrentTime] = useState<number>(0)
  const [volume, setVolumeState] = useState<number>(1)
  const [playbackRate, setPlaybackRateState] = useState<number>(1)
  const [duration, setDuration] = useState<number>(0)

  // startSource 透過 ref 持有，避免 React Compiler 把「source.onended closure 內
  // 呼叫 startSource」判成「closure before declaration」。startSourceRef.current
  // 在每次 render 都同步指向最新 useCallback。
  const startSourceRef = useRef<(segIdx: number, offsetSec: number, durationSec?: number) => void>(() => undefined)

  const ensureContext = useCallback((): AudioContext => {
    if (ctxRef.current) return ctxRef.current
    const Ctor = window.AudioContext
      ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = new Ctor()
    const mainGain = ctx.createGain()
    const segGain = ctx.createGain()
    segGain.gain.value = 1
    segGain.connect(ctx.destination)
    mainGain.connect(segGain)
    ctxRef.current = ctx
    mainGainRef.current = mainGain
    segmentGainRef.current = segGain
    return ctx
  }, [])

  const stopActive = useCallback(() => {
    const a = activeRef.current
    if (a) {
      const ctx = ctxRef.current
      if (ctx) pausedAtRef.current = a.globalStartSec + (ctx.currentTime - a.ctxAnchorSec) * rateRef.current
      try { a.source.stop() } catch { /* already stopped */ }
      a.source.disconnect()
      activeRef.current = null
    }
  }, [])

  const ensureBuffer = useCallback(async (idx: number): Promise<AudioBuffer | null> => {
    const cached = cache.get(idx)
    if (cached) return cached
    const seg = episodeRef.current?.segments[idx]
    if (!seg) return null
    if (decodingRef.current.has(idx)) {
      while (decodingRef.current.has(idx)) await new Promise(r => setTimeout(r, DECODE_POLL_MS))
      return cache.get(idx) ?? null
    }
    decodingRef.current.add(idx)
    try {
      const ctx = ensureContext()
      const res = await fetch(seg.audioUrl)
      if (!res.ok) return null
      const buf = await ctx.decodeAudioData(await res.arrayBuffer())
      cache.set(idx, buf)
      return buf
    } catch (e) {
      console.error('[useSegmentPlayer] decode failed', e)
      return null
    } finally {
      decodingRef.current.delete(idx)
    }
  }, [cache, ensureContext])

  const prefetchAround = useCallback(async (idx: number): Promise<void> => {
    const ep = episodeRef.current
    if (!ep) return
    const lo = Math.max(idx - 3, 0)
    const hi = Math.min(idx + 4, ep.segments.length - 1)
    await Promise.all(
      Array.from({ length: hi - lo + 1 }, (_, k) => {
        const i = lo + k
        return cache.has(i) || decodingRef.current.has(i) ? null : ensureBuffer(i)
      }),
    )
  }, [cache, ensureBuffer])

  const startSource = useCallback((segIdx: number, offsetSec: number, durationSec?: number) => {
    const ctx = ctxRef.current
    const mainGain = mainGainRef.current
    const buf = cache.get(segIdx)
    const seg = episodeRef.current?.segments[segIdx]
    if (!ctx || !mainGain || !buf || !seg) return

    const source = ctx.createBufferSource()
    source.buffer = buf
    source.playbackRate.value = rateRef.current
    source.connect(mainGain)
    activeRef.current = {
      source,
      ctxAnchorSec: ctx.currentTime,
      globalStartSec: seg.start + offsetSec,
    }
    isPlayingRef.current = true
    setIsPlaying(true)
    void ctx.resume()
    const remaining = durationSec !== undefined ? Math.min(durationSec, buf.duration - offsetSec) : undefined
    source.start(0, offsetSec, remaining)
    source.onended = () => {
      if (activeRef.current?.source !== source) return
      stopActive()
      const ep = episodeRef.current
      if (!ep) return
      const next = segIdxRef.current + 1
      if (next >= ep.segments.length) {
        isPlayingRef.current = false
        setIsPlaying(false)
        return
      }
      segIdxRef.current = next
      void prefetchAround(next)
      void ensureBuffer(next).then(b => {
        if (b && isPlayingRef.current) startSourceRef.current(next, 0)
      })
    }
  }, [cache, ensureBuffer, prefetchAround, stopActive])

  useEffect(() => { startSourceRef.current = startSource }, [startSource])

  const duckAndPlaySegment = useCallback(async (segIdx: number, offsetSec: number, durationSec: number) => {
    const buf = await ensureBuffer(segIdx)
    const ctx = ctxRef.current ?? ensureContext()
    void ctx.resume()
    if (!buf) return
    const mainGain = mainGainRef.current!
    const now = ctx.currentTime
    mainGain.gain.cancelScheduledValues(now)
    mainGain.gain.setValueAtTime(mainGain.gain.value, now)
    mainGain.gain.linearRampToValueAtTime(DUCK_LEVEL, now + DUCK_RAMP_SEC)
    window.setTimeout(() => {
      const t = ctx.currentTime
      mainGain.gain.cancelScheduledValues(t)
      mainGain.gain.setValueAtTime(mainGain.gain.value, t)
      mainGain.gain.linearRampToValueAtTime(1, t + DUCK_RAMP_SEC)
    }, durationSec * 1000)
    stopActive()
    segIdxRef.current = segIdx
    startSource(segIdx, offsetSec, durationSec)
  }, [ensureBuffer, ensureContext, startSource, stopActive])

  const play = useCallback(async () => {
    const ep = episodeRef.current
    if (!ep || loadState === 'loading') {
      pendingRef.current = 'play'
      return
    }
    if (loadState !== 'ready') return
    const buf = await ensureBuffer(segIdxRef.current)
    if (!buf) return
    stopActive()
    const seg = ep.segments[segIdxRef.current]
    const offsetSec = seg ? Math.max(0, Math.min(pausedAtRef.current - seg.start, seg.duration)) : 0
    startSource(segIdxRef.current, offsetSec)
    void prefetchAround(segIdxRef.current)
  }, [ensureBuffer, loadState, prefetchAround, startSource, stopActive])

  const pause = useCallback(() => {
    stopActive()
    isPlayingRef.current = false
    setIsPlaying(false)
  }, [stopActive])

  const seekTo = useCallback((globalSec: number) => {
    const ep = episodeRef.current
    if (!ep || ep.segments.length === 0) return
    let lo = 0, hi = ep.segments.length - 1, idx = 0
    while (lo <= hi) {
      const mid = (lo + hi) >> 1
      const seg = ep.segments[mid]
      if (!seg) break
      if (globalSec < seg.start) hi = mid - 1
      else if (globalSec > seg.end) { idx = mid; lo = mid + 1 }
      else { idx = mid; break }
    }
    const seg = ep.segments[idx]
    if (!seg) return
    const offsetSec = Math.max(0, Math.min(globalSec - seg.start, seg.duration))
    const wasPlaying = isPlayingRef.current
    stopActive()
    pausedAtRef.current = globalSec
    segIdxRef.current = idx
    void prefetchAround(idx)
    if (wasPlaying) void ensureBuffer(idx).then(b => { if (b) startSource(idx, offsetSec) })
  }, [ensureBuffer, prefetchAround, startSource, stopActive])

  const setPlaybackRate = useCallback((rate: number) => {
    rateRef.current = rate
    setPlaybackRateState(rate)
    if (activeRef.current) activeRef.current.source.playbackRate.value = rate
  }, [])

  const setVolume = useCallback((v: number) => {
    const clamped = Math.max(0, Math.min(1, v))
    setVolumeState(clamped)
    const sg = segmentGainRef.current
    if (sg && ctxRef.current) sg.gain.setValueAtTime(clamped, ctxRef.current.currentTime)
  }, [])

  const unlock = useCallback(async () => {
    const ctx = ensureContext()
    if (ctx.state === 'suspended') await ctx.resume()
  }, [ensureContext])

  const playSegment = useCallback((segIdx: number, offsetSec: number, durationSec: number) => {
    void duckAndPlaySegment(segIdx, offsetSec, durationSec)
  }, [duckAndPlaySegment])

  const loadEpisode = useCallback(async (episode: Episode | null) => {
    stopActive()
    cache.clear()
    decodingRef.current.clear()
    pendingRef.current = null
    episodeRef.current = episode
    segIdxRef.current = 0
    pausedAtRef.current = 0
    isPlayingRef.current = false
    setIsPlaying(false)
    setCurrentTime(0)
    setDuration(episode?.cues.at(-1)?.end ?? 0)
    if (!episode || episode.segments.length === 0) {
      setLoadState('idle')
      return
    }
    setLoadState('loading')
    const first = await ensureBuffer(0)
    if (!first) { setLoadState('error'); return }
    void prefetchAround(0)
    setLoadState('ready')
    if (pendingRef.current === 'play') {
      pendingRef.current = null
      void play()
    }
  }, [cache, ensureBuffer, play, prefetchAround, stopActive])

  // rAF 同步 currentTime
  useEffect(() => {
    let raf: number | null = null
    const tick = () => {
      const ctx = ctxRef.current
      const a = activeRef.current
      if (ctx && a && isPlayingRef.current) {
        const elapsed = (ctx.currentTime - a.ctxAnchorSec) * rateRef.current
        setCurrentTime(a.globalStartSec + elapsed)
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => { if (raf !== null) cancelAnimationFrame(raf) }
  }, [])

  return {
    loadState, isPlaying, currentTime, duration, playbackRate, volume,
    unlock, loadEpisode, play, pause, seekTo, setPlaybackRate, setVolume, playSegment,
  }
}
