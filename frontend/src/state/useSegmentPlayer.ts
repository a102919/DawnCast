/** 播放意圖層：簡化版，引擎用原生 <audio> playlist。
 *
 * 為什麼這支 hook 變得這麼短：把 mp3 載入排程交給瀏覽器（HTMLAudioElement + preload）
 * 之後，await decode 的空窗消失了，AbortController 漏斗沒必要，pending-play 佇列也
 * 不必要——loadEpisode 改成同步切換集數（讓瀏覽器在背景繼續載，按 play 時元素已經
 * 有 metadata 可播就直接播，沒有的讓瀏覽器繼續載，使用者按播放就是「解除自動等
 * 待」的語意，FlashcardRoute 等 UI 元件必須先 loadEpisode 再 play 才能用上）。
 *
 * identity guard（新 handle 已經同步取代掉舊的，舊的 onended 才姍姍來遲觸發）由
 * engine 內的 token WeakMap 處理，這層不再比對。
 *
 * 句間停頓：seg.start/end 由後端 build_timeline 算好時已內建間隔（一般 0.3s／
 * 章節轉換 0.7s，見 subtitles.py），這裡照樣子等，不訂數字。
 *
 * iOS Safari：首次 play() 必須在 click handler 同步路徑內呼叫，且 engine 內每個
 * 元素都需要獨立拿授權——loadEpisode 之後第一個 play() 必須先過 engine.unlock()
 * 三元素輪轉一輪，後續的自動接播就靠瀏覽器保留的授權。
 *
 * playbackRate 走 el.playbackRate（pausedAt 維持 1x 語意不變；engine 內的
 * currentPositionSec 直接讀 el.currentTime，已自動含 rate 的時間轉換）。
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { Episode } from '../types/episode'
import { createAudioEngine, type PlaybackHandle } from './audioEngine'
import { playerReducer, toPublicFields, findSegmentForTime, clampOffset, initialMainPlayerState } from './playerIntent'

export type SegmentLoadState = 'idle' | 'loading' | 'ready' | 'error'

export interface SegmentPlayer {
  readonly loadState: SegmentLoadState
  readonly isPlaying: boolean
  readonly currentTime: number
  readonly duration: number
  readonly playbackRate: number
  readonly muted: boolean
  unlock(): void
  loadEpisode(episode: Episode | null): void
  play(): void
  pause(): void
  seekTo(globalSec: number): void
  setPlaybackRate(rate: number): void
  setMuted(m: boolean): void
  playSegment(segmentIdx: number, offsetSec: number, durationSec: number): void
}

export function useSegmentPlayer(): SegmentPlayer {
  const [engine] = useState(() => createAudioEngine())

  const episodeRef = useRef<Episode | null>(null)
  const activeRef = useRef<PlaybackHandle | null>(null)
  const segIdxRef = useRef<number>(0)
  const isPlayingRef = useRef<boolean>(false)
  const rateRef = useRef<number>(1)
  const mutedRef = useRef<boolean>(false)
  /** 暫停/seek/換段當下的全域播放位置，play() 靠它算 resume 要從段內哪個 offset 接續。 */
  const pausedAtRef = useRef<number>(0)

  const [state, dispatch] = useReducer(playerReducer, initialMainPlayerState)
  const { loadState, isPlaying } = toPublicFields(state)
  const [currentTime, setCurrentTime] = useState<number>(0)
  const [muted, setMutedState] = useState<boolean>(false)
  const [playbackRate, setPlaybackRateState] = useState<number>(1)
  const [duration, setDuration] = useState<number>(0)

  // startMain 透過 ref 持有，避免自動接播的 closure 抓舊版本。
  const startMainRef = useRef<(segIdx: number, offsetSec: number) => void>(() => undefined)

  const stopActive = useCallback(() => {
    const a = activeRef.current
    if (!a) return
    const pos = engine.stop(a)
    pausedAtRef.current = pos
    activeRef.current = null
  }, [engine])

  const startMain = useCallback((segIdx: number, offsetSec: number) => {
    const seg = episodeRef.current?.segments[segIdx]
    if (!seg) return
    const handle = engine.startPlayback(
      { url: seg.audioUrl, globalStartSec: seg.start, offsetSec, rate: rateRef.current },
      () => {
        // 檔案「真」播完觸發——瀏覽器已經保證最後一個 sample 出聲，邊界切字物理上不可能發生。
        const ep = episodeRef.current
        if (!ep) return
        const cur = segIdxRef.current
        const next = cur + 1
        if (next >= ep.segments.length) {
          isPlayingRef.current = false
          dispatch({ type: 'PLAYBACK_STOPPED' })
          return
        }
        segIdxRef.current = next
        const gapSec = Math.max(0, ep.segments[next].start - ep.segments[cur].end)
        window.setTimeout(() => {
          if (!isPlayingRef.current) return
          startMainRef.current(next, 0)
        }, (gapSec / rateRef.current) * 1000)
      },
      () => {
        // play() promise rejected（通常是 iOS 沒拿到授權、自動接播時環境拒絕）
        isPlayingRef.current = false
        dispatch({ type: 'PLAYBACK_STOPPED' })
      },
    )
    if (!handle) return
    activeRef.current = handle
    isPlayingRef.current = true
    dispatch({ type: 'PLAYBACK_STARTED' })
  }, [engine])

  // pull-based progress：FOUC 與跨分頁同步需求都不算高，每 100ms 抓一次元素時間就夠。
  useEffect(() => {
    const tick = () => {
      const a = activeRef.current
      if (a && isPlayingRef.current) setCurrentTime(engine.currentPositionSec(a))
    }
    const id = window.setInterval(tick, 100)
    return () => window.clearInterval(id)
  }, [engine])

  // 自動接播的定時 callback 會讀到這個 ref；沒寫 effect 把 startMain 掛上去，
  // ref 永久停在 `() => undefined` 預設值，trace 看到 function typeof 但呼叫沒副作用。
  useEffect(() => {
    startMainRef.current = startMain
  }, [startMain])

  const playSegment = useCallback((segIdx: number, offsetSec: number, durationSec: number) => {
    const seg = episodeRef.current?.segments[segIdx]
    if (!seg) return
    engine.startPlayback(
      { url: seg.audioUrl, globalStartSec: seg.start, offsetSec, durationSec, rate: rateRef.current },
      () => { /* 試聽播完自然結束：試聽元素不影響主播放，什麼都不做 */ },
      () => { /* 試聽 play() 拒絕：靜默忽略 */ },
    )
  }, [engine])

  const play = useCallback(() => {
    const ep = episodeRef.current
    if (!ep) return
    const seg = ep.segments[segIdxRef.current]
    if (!seg) return
    // iOS 必備：在 click handler 同步路徑內對「目標 URL」做 play/pause，
    // 拿到對該 src 的授權。光 SILENT_WAV 解的鎖不算數（見 audioEngine.unlock 註解）。
    engine.unlock(seg.audioUrl)
    const offsetSec = clampOffset(pausedAtRef.current, seg)
    startMain(segIdxRef.current, offsetSec)
  }, [engine, startMain])

  const pause = useCallback(() => {
    isPlayingRef.current = false
    stopActive()
    dispatch({ type: 'PLAYBACK_STOPPED' })
  }, [stopActive])

  const seekTo = useCallback((globalSec: number) => {
    const ep = episodeRef.current
    if (!ep || ep.segments.length === 0) return
    const idx = findSegmentForTime(ep.segments, globalSec)
    const seg = ep.segments[idx]
    if (!seg) return
    const offsetSec = clampOffset(globalSec, seg)
    const wasPlaying = isPlayingRef.current
    stopActive()
    pausedAtRef.current = globalSec
    segIdxRef.current = idx
    setCurrentTime(globalSec)
    if (wasPlaying) startMain(idx, offsetSec)
  }, [startMain, stopActive])

  const setPlaybackRate = useCallback((rate: number) => {
    rateRef.current = rate
    setPlaybackRateState(rate)
    if (activeRef.current) engine.setRate(activeRef.current, rate)
  }, [engine])

  const setMuted = useCallback((m: boolean) => {
    mutedRef.current = m
    setMutedState(m)
    engine.setMuted(m)
  }, [engine])

  const unlock = useCallback(() => { engine.unlock() }, [engine])

  const loadEpisode = useCallback((episode: Episode | null) => {
    stopActive()
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
    // 沒有解碼空窗還是走 LOAD_STARTED → LOAD_SUCCEEDED 兩步，維持 reducer
    // 收斂語意（idle → loading → paused），並讓 playerIntent 窮舉測試穩定。
    dispatch({ type: 'LOAD_STARTED' })
    dispatch({ type: 'LOAD_SUCCEEDED' })
  }, [stopActive])

  return {
    loadState, isPlaying, currentTime, duration, playbackRate, muted,
    unlock, loadEpisode, play, pause, seekTo, setPlaybackRate, setMuted, playSegment,
  }
}
