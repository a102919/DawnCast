/** 播放意圖層：包一顆 audioEngine，把 DOM 事件鏡像成 React 狀態。
 *
 * 整集單一 mp3 之後，這層不再管「哪一段 mp3、offset 換算、自動接播下一段」——
 * el.currentTime 直接就是全域播放位置，seekTo 是唯一一種跳轉。currentTime 沒有
 * 100ms 輪詢，改成事件驅動（timeupdate/seeked 觸發才更新），isPlaying 由
 * play/pause/ended 事件直接 dispatch 進 playerIntent 的 reducer，元素本身
 * （el.paused）才是「正不正在播」的事實，這層只是鏡像。
 *
 * 所有 play() 呼叫都在使用者手勢的同步呼叫堆疊內（點播放鍵、點字幕行、點單字…），
 * 不再有「自動接播不在 gesture 內」這件事，介面上不再有 unlock()。
 */

import { useCallback, useReducer, useRef, useState } from 'react'
import type { Episode } from '../types/episode'
import { createAudioEngine } from './audioEngine'
import { playerReducer, toPublicFields, initialMainPlayerState } from './playerIntent'

export type SegmentLoadState = 'idle' | 'loading' | 'ready' | 'error'

export interface SegmentPlayer {
  readonly loadState: SegmentLoadState
  readonly isPlaying: boolean
  readonly currentTime: number
  readonly duration: number
  readonly playbackRate: number
  readonly muted: boolean
  loadEpisode(episode: Episode | null): void
  play(): void
  pause(): void
  seekTo(globalSec: number): void
  /** 練習模式 word click：跳到 (cue.start + word.start)。需要 cue.words 有值；
   *  舊集 / edge-tts fallback 沒 word boundary 時退回 cue.start（整句 click 行為）。
   *  回 true 表示有跳到精確字詞位置，false 表示資料不足只做到 cue-level seek。 */
  seekToWord(cueIdx: number, wordIdx: number): boolean
  setPlaybackRate(rate: number): void
  setMuted(m: boolean): void
  /** 試聽抽樣（給 PronounceButton / WordCardPanel / ReplayAudioButton 用）：
   *  playClip(cue.start, cue.end - cue.start) 播整句、playClip(cue.start + offset, 0.6)
   *  播單字附近片段。不影響主播放游標。 */
  playClip(startSec: number, durationSec: number): void
}

/** Dev 後端 public_base_url 預設 localhost:8000，audio 元素跨原始會被擋；
 *  把 host 換成當前 origin 讓 vite /mock-r2 proxy 接走。prod 已是同 origin /
 *  Cloudflare R2 簽章網域，URL 不會命中。URL parse 失敗時原樣回傳，不擋播放。 */
function toSameOriginAudioUrl(url: string): string {
  if (typeof window === 'undefined') return url
  try {
    const u = new URL(url)
    if (u.host === 'localhost:8000' || u.host === '127.0.0.1:8000') {
      u.host = window.location.host
    }
    return u.toString()
  } catch {
    return url
  }
}

export function useSegmentPlayer(): SegmentPlayer {
  const [state, dispatch] = useReducer(playerReducer, initialMainPlayerState)
  const { loadState, isPlaying } = toPublicFields(state)
  const [currentTime, setCurrentTime] = useState<number>(0)
  const [duration, setDuration] = useState<number>(0)
  const [playbackRate, setPlaybackRateState] = useState<number>(1)
  const [muted, setMutedState] = useState<boolean>(false)

  const episodeRef = useRef<Episode | null>(null)

  // engine 只建一次；handlers 內用同一個 useState 初始化函式內的區域變數 `engine`
  // 自我參照（宣告完成前不會被呼叫，事件是非同步觸發，statement 執行完 engine 早
  // 已賦值），藉此讀 engine.currentTime()/duration() 當事實來源，不必另外用 ref。
  const [engine] = useState(() => {
    const engine = createAudioEngine({
      onTimeUpdate: () => {
        setCurrentTime(engine.currentTime())
        const d = engine.duration()
        if (Number.isFinite(d) && d > 0) setDuration(d)
      },
      onSeeked: () => setCurrentTime(engine.currentTime()),
      onPlay: () => dispatch({ type: 'PLAYBACK_STARTED' }),
      onPause: () => dispatch({ type: 'PLAYBACK_STOPPED' }),
      onEnded: () => dispatch({ type: 'PLAYBACK_STOPPED' }),
    })
    return engine
  })

  const loadEpisode = useCallback((episode: Episode | null) => {
    episodeRef.current = episode
    setCurrentTime(0)
    if (!episode) {
      setDuration(0)
      dispatch({ type: 'LOAD_CLEARED' })
      return
    }
    dispatch({ type: 'LOAD_STARTED' })
    const url = episode.audioUrl ? toSameOriginAudioUrl(episode.audioUrl) : null
    if (!url) {
      // 集數存在但沒有可播的整集音檔（後端還沒產完 / 舊集 backfill 未涵蓋）：
      // 這是真的錯誤狀態，不是「什麼都沒載入」，UI 要顯示錯誤而不是空白。
      setDuration(0)
      dispatch({ type: 'LOAD_FAILED' })
      return
    }
    // metadata 到之前用 cues 算 duration；到了之後的事實來源是 el.duration
    // （onTimeUpdate 會接手更新），兩者理論上相等——後端保證 cues[-1].end === 物理時長。
    setDuration(episode.cues.at(-1)?.end ?? 0)
    engine.load(url)
    dispatch({ type: 'LOAD_SUCCEEDED' })
  }, [engine])

  const play = useCallback(() => {
    if (!episodeRef.current?.audioUrl) return
    void engine.play().catch((err: unknown) => {
      // AbortError 是 pause()/換集數搶在 play() 落定前發生的預期副作用；
      // 其他（多半是 iOS NotAllowedError）保留 log 供排查，實機 debug window 小。
      const name = err instanceof Error ? err.name : undefined
      if (name !== 'AbortError') console.error('[useSegmentPlayer] play() rejected', err)
    })
  }, [engine])

  const pause = useCallback(() => { engine.pause() }, [engine])

  const seekTo = useCallback((globalSec: number) => {
    if (!episodeRef.current) return
    engine.seek(Math.max(0, globalSec))
  }, [engine])

  const seekToWord = useCallback((cueIdx: number, wordIdx: number): boolean => {
    const ep = episodeRef.current
    if (!ep) return false
    const cue = ep.cues[cueIdx]
    if (!cue) return false
    const words = cue.words
    if (!words) {
      seekTo(cue.start)
      return false
    }
    const word = words[wordIdx]
    if (!word) return false
    seekTo(cue.start + word.start)
    return true
  }, [seekTo])

  const setPlaybackRate = useCallback((rate: number) => {
    setPlaybackRateState(rate)
    engine.setRate(rate)
  }, [engine])

  const setMuted = useCallback((m: boolean) => {
    setMutedState(m)
    engine.setMuted(m)
  }, [engine])

  const playClip = useCallback((startSec: number, durationSec: number) => {
    engine.playClip(startSec, durationSec)
  }, [engine])

  return {
    loadState, isPlaying, currentTime, duration, playbackRate, muted,
    loadEpisode, play, pause, seekTo, seekToWord, setPlaybackRate, setMuted, playClip,
  }
}
