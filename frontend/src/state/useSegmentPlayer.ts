/** 播放意圖層：lazy decode + LRU 8 段 + ducking + gesture unlock 這些 Web Audio 細節
 *  全部搬進 audioEngine.ts（框架無關）；主播放狀態（loadState/isPlaying）走
 *  playerIntent.ts 的 reducer；這裡只管「使用者/系統的播放意圖」該怎麼轉譯成對
 *  engine/reducer 的呼叫，以及跟 React state 同步。
 *
 * 取消機制：AbortController 取代世代計數器。beginIntent() 是唯一建立新播放意圖的入口——
 * 呼叫時會先 abort 上一個 controller，任何橫跨 await 的非同步接續（ensureBuffer 之後）
 * 都要在 await 後檢查 signal.aborted，不同就代表在等待期間又有新的播放意圖蓋過去了，
 * 直接放棄不執行 —— 否則會出現「segment 自然結束觸發自動接播」跟「使用者剛好在那個
 * 當下暫停又立刻恢復播放」兩條非同步鏈同時對同一段呼叫 startSource，疊出兩個同時在播的
 * source，聽起來像卡住重複播放。
 *
 * playbackRate 走 source.playbackRate.value；currentTime = seg.start + offsetSec +
 * (ctx.currentTime - ctxAnchor) * playbackRate——offsetSec 由 audioEngine 收進
 * handle.anchorPositionSec，漏掉它等於宣稱每次都從段頭起播。單字抽樣 playSegment 走 ducking：
 * main gain ramp 50ms 降到 0.3，duration 結束 ramp 回 1.0。
 *
 * iOS Safari：首次 play() 必須 click handler 同步路徑內 ctx.resume()，否則 gesture 解鎖
 * 失效——engine.unlock()/首次 startPlayback 的同步前綴（ensureContext/audioEl.play()）
 * 必須維持在呼叫者的同一個呼叫堆疊內執行，不能被路由過任何 dispatch/useEffect 間接層。
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { Episode } from '../types/episode'
import { createAudioEngine, DUCK_RAMP_SEC, type AudioEngine, type PlaybackHandle } from './audioEngine'
import { playerReducer, toPublicFields, findSegmentForTime, clampOffset, initialMainPlayerState } from './playerIntent'

export type SegmentLoadState = 'idle' | 'loading' | 'ready' | 'error'

const DUCK_LEVEL = 0.3

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

/** 意圖已過期時，讀出來的 signal 一律當成「已中止」處理的安全預設值。 */
const ALREADY_ABORTED_SIGNAL: AbortSignal = (() => {
  const c = new AbortController()
  c.abort()
  return c.signal
})()

/** 建立播放來源 + 掛上「自然播完」callback，identity guard（新 source 已經同步取代掉
 *  舊的，舊的 onended 才姍姍來遲觸發）在這裡統一處理，onNaturalEnd 不用重複判斷。
 *  框架無關、不含 dispatch——是 startSource（主播放）跟 duckAndPlaySegment（試聽）
 *  共用的核心機制。 */
function startCore(
  deps: { readonly engine: AudioEngine; readonly activeRef: RefObject<PlaybackHandle | null>; readonly activeIsPreviewRef: RefObject<boolean> },
  args: { readonly url: string; readonly globalStartSec: number; readonly offsetSec: number; readonly durationSec?: number; readonly rate: number },
  onNaturalEnd: () => void,
): PlaybackHandle | null {
  const handle = deps.engine.startPlayback(args)
  if (!handle) return null
  deps.activeRef.current = handle
  // 「這個 handle 是不是試聽」直接由 durationSec 有無推導（只有試聽會限定播放長度，
  // 主播放一律播到段尾），不另開一個要跟 activeRef 人工同步的旗標——兩者在同一行設定，
  // 就不會有「換了 handle 忘了換旗標」的漂移。
  deps.activeIsPreviewRef.current = args.durationSec !== undefined
  handle.source.onended = () => {
    if (deps.activeRef.current !== handle) return
    onNaturalEnd()
  }
  return handle
}

interface PreviewDeps {
  readonly engine: AudioEngine
  readonly episodeRef: RefObject<Episode | null>
  readonly rateRef: RefObject<number>
  readonly activeRef: RefObject<PlaybackHandle | null>
  readonly activeIsPreviewRef: RefObject<boolean>
  readonly duckTimeoutRef: RefObject<number | null>
  readonly ensureBuffer: (idx: number) => Promise<AudioBuffer | null>
  readonly beginIntent: () => AbortSignal
  readonly stopActive: () => void
}

/** 單字/片語試聽（發音按鈕/字卡重播）。模組層級函式，deps 刻意不含 dispatch 與 segIdxRef——
 *  這個函式的作用域裡物理上沒有這兩個自由變數，想動全域 isPlaying/loadState 或主播放的
 *  段落游標會直接編譯不過，不是「忘了加判斷」防得住的層級。播完也不會自動接播下一段，
 *  因為這裡根本沒有 segIdx-advance 的邏輯可以呼叫。 */
async function duckAndPlaySegment(deps: PreviewDeps, segIdx: number, offsetSec: number, durationSec: number): Promise<void> {
  const signal = deps.beginIntent()
  const buf = await deps.ensureBuffer(segIdx)
  const ctx = deps.engine.ensureContext()
  void ctx.resume()
  if (!buf || signal.aborted) return
  deps.stopActive() // 先處理掉上一個還沒回滿音量的 duck（見 stopActive 內的邏輯），再排這次的
  deps.engine.duckDown(DUCK_LEVEL, DUCK_RAMP_SEC)
  deps.duckTimeoutRef.current = window.setTimeout(() => {
    deps.duckTimeoutRef.current = null
    deps.engine.restoreVolume(DUCK_RAMP_SEC)
  }, durationSec * 1000)
  const seg = deps.episodeRef.current?.segments[segIdx]
  if (!seg) return
  startCore({ engine: deps.engine, activeRef: deps.activeRef, activeIsPreviewRef: deps.activeIsPreviewRef }, {
    url: seg.audioUrl, globalStartSec: seg.start, offsetSec, durationSec, rate: deps.rateRef.current,
  }, deps.stopActive)
}

export function useSegmentPlayer(): SegmentPlayer {
  const [engine] = useState<AudioEngine>(() => createAudioEngine())

  const episodeRef = useRef<Episode | null>(null)
  const activeRef = useRef<PlaybackHandle | null>(null)
  const segIdxRef = useRef<number>(0)
  const isPlayingRef = useRef<boolean>(false)
  const rateRef = useRef<number>(1)
  const pendingRef = useRef<'play' | null>(null)
  /** 暫停/seek/換段當下的全域播放位置，play() 靠它算 resume 要從段內哪個 offset 接續，
   *  不能吃 currentTime state（rAF 節流，可能落後一幀）。 */
  const pausedAtRef = useRef<number>(0)
  /** 播放意圖的取消 token 來源。beginIntent() 是唯一建立新意圖的入口。 */
  const controllerRef = useRef<AbortController | null>(null)
  /** duckAndPlaySegment 排定的「duration 結束後把音量 ramp 回 1.0」timeout handle。
   *  任何新播放意圖（stopActive 涵蓋的所有情況）都要立刻取消並馬上恢復滿音量，不能放著等
   *  原本排定的時間到才回復，不然會出現「恢復播放後音量卡在 duck 音量一段時間」的問題。 */
  const duckTimeoutRef = useRef<number | null>(null)
  /** activeRef 目前指的是不是「試聽」handle（見 startCore）。試聽是一次性的抽樣播放，
   *  不屬於主播放游標，停止時不可把它的位置寫回 pausedAtRef。 */
  const activeIsPreviewRef = useRef<boolean>(false)

  const [state, dispatch] = useReducer(playerReducer, initialMainPlayerState)
  const { loadState, isPlaying } = toPublicFields(state)
  const [currentTime, setCurrentTime] = useState<number>(0)
  const [volume, setVolumeState] = useState<number>(1)
  const [playbackRate, setPlaybackRateState] = useState<number>(1)
  const [duration, setDuration] = useState<number>(0)

  // startSource 透過 ref 持有，避免 React Compiler 把「source.onended closure 內
  // 呼叫 startSource」判成「closure before declaration」。startSourceRef.current
  // 在每次 render 都同步指向最新 useCallback。
  const startSourceRef = useRef<(segIdx: number, offsetSec: number) => void>(() => undefined)

  const beginIntent = useCallback((): AbortSignal => {
    controllerRef.current?.abort()
    const c = new AbortController()
    controllerRef.current = c
    return c.signal
  }, [])

  const stopActive = useCallback(() => {
    if (duckTimeoutRef.current !== null) {
      window.clearTimeout(duckTimeoutRef.current)
      duckTimeoutRef.current = null
      engine.restoreVolume(DUCK_RAMP_SEC)
    }
    const a = activeRef.current
    if (a) {
      const pos = engine.stop(a, rateRef.current)
      // 只有主播放的停止位置算數。試聽（字卡發音／重聽這句）結束時若把它的位置寫回
      // pausedAtRef，關掉字卡後的「續播」會從剛才試聽的那一行接續，而不是原本暫停的位置。
      if (!activeIsPreviewRef.current) pausedAtRef.current = pos
      activeRef.current = null
    }
  }, [engine])

  const ensureBuffer = useCallback(async (idx: number): Promise<AudioBuffer | null> => {
    const seg = episodeRef.current?.segments[idx]
    if (!seg) return null
    return engine.getBuffer(seg.audioUrl)
  }, [engine])

  const prefetchAround = useCallback(async (idx: number): Promise<void> => {
    const ep = episodeRef.current
    if (!ep) return
    const lo = Math.max(idx - 3, 0)
    const hi = Math.min(idx + 4, ep.segments.length - 1)
    await Promise.all(Array.from({ length: hi - lo + 1 }, (_, k) => ensureBuffer(lo + k)))
  }, [ensureBuffer])

  const startSource = useCallback((segIdx: number, offsetSec: number) => {
    const seg = episodeRef.current?.segments[segIdx]
    if (!seg) return
    const handle = startCore({ engine, activeRef, activeIsPreviewRef }, {
      url: seg.audioUrl, globalStartSec: seg.start, offsetSec, rate: rateRef.current,
    }, () => {
      stopActive()
      const ep = episodeRef.current
      if (!ep) return
      const next = segIdxRef.current + 1
      if (next >= ep.segments.length) {
        engine.suspend() // 整集播完同樣收掉輸出鏈，理由同 pause()
        isPlayingRef.current = false
        dispatch({ type: 'PLAYBACK_STOPPED' })
        return
      }
      const curIdx = segIdxRef.current
      segIdxRef.current = next
      const signal = controllerRef.current?.signal ?? ALREADY_ABORTED_SIGNAL
      void prefetchAround(next)
      // 句間停頓：seg.start/end 由後端 build_timeline 算好時已內建間隔（一般 0.3s／
      // 章節轉換 0.7s，見 subtitles.py），這裡照樣子等，不用自己另外訂數字。
      const gapSec = Math.max(0, ep.segments[next].start - ep.segments[curIdx].end)
      void ensureBuffer(next).then(b => {
        if (!b || !isPlayingRef.current || signal.aborted) return
        window.setTimeout(() => {
          if (isPlayingRef.current && !signal.aborted) startSourceRef.current(next, 0)
        }, (gapSec / rateRef.current) * 1000)
      })
    })
    if (!handle) return
    isPlayingRef.current = true
    dispatch({ type: 'PLAYBACK_STARTED' })
  }, [engine, ensureBuffer, prefetchAround, stopActive])

  useEffect(() => { startSourceRef.current = startSource }, [startSource])

  const playSegment = useCallback((segIdx: number, offsetSec: number, durationSec: number) => {
    void duckAndPlaySegment({
      engine, episodeRef, rateRef, activeRef, activeIsPreviewRef, duckTimeoutRef, ensureBuffer, beginIntent, stopActive,
    }, segIdx, offsetSec, durationSec)
  }, [engine, ensureBuffer, beginIntent, stopActive])

  // play() 的核心動作：從 pausedAtRef 記錄的位置接續播放。抽出來獨立於 loadState 之外，
  // 是因為 loadEpisode() 尾端「解碼完成後如果有排隊的播放意圖就自動接播」也要呼叫這段——
  // 如果讓它去呼叫 play()，play() 依賴 loadState 每次都拿新身分，loadEpisode 那個 async
  // function 呼叫當下閉包捕捉到的 play 版本，其內部 loadState 還停在呼叫當下的舊值（通常
  // 是 loading 之前），不會是幾行之後 dispatch LOAD_SUCCEEDED 剛設的新值，導致自動接播的
  // play() 一律提早 return，形同沒接上。resumeFromPausedPosition 不吃 loadState，identity
  // 穩定，兩邊呼叫都不會有這個 stale closure 問題。
  const resumeFromPausedPosition = useCallback(async () => {
    const ep = episodeRef.current
    if (!ep) return
    const signal = beginIntent()
    const buf = await ensureBuffer(segIdxRef.current)
    if (!buf || signal.aborted) return
    stopActive()
    const seg = ep.segments[segIdxRef.current]
    const offsetSec = seg ? clampOffset(pausedAtRef.current, seg) : 0
    startSource(segIdxRef.current, offsetSec)
    void prefetchAround(segIdxRef.current)
  }, [beginIntent, ensureBuffer, prefetchAround, startSource, stopActive])

  const play = useCallback(async () => {
    const ep = episodeRef.current
    if (!ep || loadState === 'loading') {
      pendingRef.current = 'play'
      return
    }
    if (loadState !== 'ready') return
    await resumeFromPausedPosition()
  }, [loadState, resumeFromPausedPosition])

  const pause = useCallback(() => {
    beginIntent()
    pendingRef.current = null
    stopActive()
    // 停完 source 再收掉整條輸出鏈（順序不能反：suspend 後 ctx.currentTime 凍結，
    // stopActive 算 pausedAt 位置會算錯）。少了這步，iOS 的音訊 session 在暫停後
    // 仍維持「播放中」，殘留 buffer 可能被卡住無限重播。
    engine.suspend()
    isPlayingRef.current = false
    dispatch({ type: 'PLAYBACK_STOPPED' })
  }, [beginIntent, engine, stopActive])

  const seekTo = useCallback((globalSec: number) => {
    const ep = episodeRef.current
    if (!ep || ep.segments.length === 0) return
    const idx = findSegmentForTime(ep.segments, globalSec)
    const seg = ep.segments[idx]
    if (!seg) return
    const offsetSec = clampOffset(globalSec, seg)
    const wasPlaying = isPlayingRef.current
    const signal = beginIntent()
    stopActive()
    pausedAtRef.current = globalSec
    segIdxRef.current = idx
    // 同步把 currentTime 推到新位置，不等 rAF：暫停時 seek 根本沒有 activeRef，
    // rAF 那圈永遠不會跑到 setCurrentTime，進度條會卡在舊位置；播放時則是要讓
    // 依賴 currentTime 的邏輯（單句循環）立刻讀到新位置，而不是上一幀的舊值。
    setCurrentTime(globalSec)
    void prefetchAround(idx)
    if (wasPlaying) void ensureBuffer(idx).then(b => { if (b && !signal.aborted) startSource(idx, offsetSec) })
  }, [beginIntent, ensureBuffer, prefetchAround, startSource, stopActive])

  const setPlaybackRate = useCallback((rate: number) => {
    rateRef.current = rate
    setPlaybackRateState(rate)
    if (activeRef.current) engine.setRate(activeRef.current, rate)
  }, [engine])

  const setVolume = useCallback((v: number) => {
    const clamped = Math.max(0, Math.min(1, v))
    setVolumeState(clamped)
    engine.setVolume(clamped)
  }, [engine])

  const unlock = useCallback(async () => {
    await engine.unlock()
  }, [engine])

  const loadEpisode = useCallback(async (episode: Episode | null) => {
    const signal = beginIntent()
    stopActive()
    engine.clearCache()
    pendingRef.current = null
    episodeRef.current = episode
    segIdxRef.current = 0
    pausedAtRef.current = 0
    isPlayingRef.current = false
    setCurrentTime(0)
    setDuration(episode?.cues.at(-1)?.end ?? 0)
    if (!episode || episode.segments.length === 0) {
      dispatch({ type: 'LOAD_CLEARED' })
      return
    }
    dispatch({ type: 'LOAD_STARTED' })
    const first = await ensureBuffer(0)
    if (signal.aborted) return // 這期間又有更新的 loadEpisode/play/pause/seek 蓋過去了，這次已過期
    if (!first) { dispatch({ type: 'LOAD_FAILED' }); return }
    void prefetchAround(0)
    dispatch({ type: 'LOAD_SUCCEEDED' })
    if (pendingRef.current === 'play') {
      pendingRef.current = null
      void resumeFromPausedPosition()
    }
  }, [beginIntent, engine, ensureBuffer, prefetchAround, resumeFromPausedPosition, stopActive])

  // rAF 同步 currentTime
  useEffect(() => {
    let raf: number | null = null
    const tick = () => {
      const a = activeRef.current
      if (a && isPlayingRef.current) {
        setCurrentTime(engine.currentPositionSec(a, rateRef.current))
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => { if (raf !== null) cancelAnimationFrame(raf) }
  }, [engine])

  return {
    loadState, isPlaying, currentTime, duration, playbackRate, volume,
    unlock, loadEpisode, play, pause, seekTo, setPlaybackRate, setVolume, playSegment,
  }
}
