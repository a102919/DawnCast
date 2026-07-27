import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { storageGet, storageSet } from '../lib/storage'
import { PlayerContext, type PlayerContextValue } from './playerContextValue'
import { useActivity } from './useActivity'
import type { Episode } from '../types/episode'

const LS_KEY_CURRENT_TIME = 'dawncast:player:currentTime'
const LS_KEY_LAST_EPISODE_ID = 'dawncast:player:lastEpisodeId'
const PROGRESS_THROTTLE_MS = 200

type SavedProgress = {
  readonly episodeId: string
  readonly currentTime: number
}

export function PlayerProvider({ children }: { readonly children: ReactNode }) {
  const videoRef = useRef<HTMLMediaElement | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRateState] = useState(1)
  const [currentEpisode, setCurrentEpisodeState] = useState<Episode | null>(null)
  const progressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const animationFrameRef = useRef<number | null>(null)
  const lastSavedTimeRef = useRef<number>(0)
  const currentEpisodeIdRef = useRef<string | null>(null)
  const { lastPlayedEpisodeId, lastPlayedPosition, setLastPlayed } = useActivity()

  const persistProgress = useCallback((
    time: number,
    episodeId: string | null,
    opts?: { readonly force?: boolean },
  ) => {
    if (!episodeId) return
    if (!opts?.force && Math.abs(time - lastSavedTimeRef.current) < 0.5) return
    lastSavedTimeRef.current = time
    storageSet<SavedProgress>(LS_KEY_CURRENT_TIME, { episodeId, currentTime: time })
    storageSet<string>(LS_KEY_LAST_EPISODE_ID, episodeId)
    // API 呼叫另開節流（見 setLastPlayed 內部），localStorage 寫入頻率不受影響。
    setLastPlayed(episodeId, time, opts)
  }, [setLastPlayed])

  useEffect(() => {
    // 換分頁 / 關閉分頁前強制 flush 一次進度，bypass setLastPlayed 內部節流，
    // 避免換裝置後遺失最後幾秒的播放進度。
    const flush = () => {
      const el = videoRef.current
      if (el && currentEpisodeIdRef.current) {
        persistProgress(el.currentTime, currentEpisodeIdRef.current, { force: true })
      }
    }
    document.addEventListener('visibilitychange', flush)
    window.addEventListener('pagehide', flush)
    return () => {
      document.removeEventListener('visibilitychange', flush)
      window.removeEventListener('pagehide', flush)
    }
  }, [persistProgress])

  const stopSyncLoop = useCallback(() => {
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }
  }, [])

  const startSyncLoop = useCallback((el: HTMLMediaElement) => {
    if (animationFrameRef.current !== null) return

    const tick = () => {
      if (videoRef.current !== el || el.paused || el.ended) {
        animationFrameRef.current = null
        return
      }
      setCurrentTime(el.currentTime)
      animationFrameRef.current = requestAnimationFrame(tick)
    }

    animationFrameRef.current = requestAnimationFrame(tick)
  }, [])

  const setVideoRef = useCallback((el: HTMLMediaElement | null) => {
    const previous = videoRef.current
    if (previous) {
      previous.ontimeupdate = null
      previous.onplay = null
      previous.onpause = null
      previous.onended = null
      previous.onloadedmetadata = null
      previous.onloadstart = null
      previous.onseeking = null
      previous.onseeked = null
    }
    stopSyncLoop()
    if (progressTimerRef.current) {
      clearTimeout(progressTimerRef.current)
      progressTimerRef.current = null
    }

    videoRef.current = el
    setCurrentTime(0)
    setDuration(0)
    setIsPlaying(false)
    if (!el) return

    const syncTime = () => {
      const t = el.currentTime
      setCurrentTime(t)
      if (progressTimerRef.current) clearTimeout(progressTimerRef.current)
      progressTimerRef.current = setTimeout(() => {
        persistProgress(t, currentEpisodeIdRef.current)
      }, PROGRESS_THROTTLE_MS)
    }

    const resetMediaState = () => {
      stopSyncLoop()
      if (progressTimerRef.current) {
        clearTimeout(progressTimerRef.current)
        progressTimerRef.current = null
      }
      setCurrentTime(0)
      setDuration(0)
      setIsPlaying(false)
    }

    el.onloadstart = resetMediaState
    el.ontimeupdate = syncTime
    el.onseeking = syncTime
    el.onseeked = syncTime
    el.onplay = () => {
      setIsPlaying(true)
      startSyncLoop(el)
    }
    el.onpause = () => {
      stopSyncLoop()
      setIsPlaying(false)
      syncTime()
      persistProgress(el.currentTime, currentEpisodeIdRef.current, { force: true })
    }
    el.onended = () => {
      stopSyncLoop()
      setIsPlaying(false)
      syncTime()
      persistProgress(el.currentTime, currentEpisodeIdRef.current, { force: true })
    }
    el.onloadedmetadata = () => {
      setDuration(Number.isFinite(el.duration) ? el.duration : 0)
      syncTime()
    }

    if (el.readyState >= 1) {
      setDuration(Number.isFinite(el.duration) ? el.duration : 0)
      syncTime()
    }
  }, [persistProgress, startSyncLoop, stopSyncLoop])

  const seekTo = useCallback((time: number) => {
    const el = videoRef.current
    if (!el || !Number.isFinite(time)) return

    const max = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : Infinity
    const nextTime = Math.min(Math.max(0, time), max)
    el.currentTime = nextTime
    persistProgress(nextTime, currentEpisodeIdRef.current)
    setCurrentTime(nextTime)
  }, [persistProgress])

  const play = useCallback(() => {
    const promise = videoRef.current?.play()
    if (promise) {
      void promise.catch(() => setIsPlaying(false))
    }
  }, [])

  const pause = useCallback(() => {
    videoRef.current?.pause()
  }, [])

  const setPlaybackRate = useCallback((rate: number) => {
    setPlaybackRateState(rate)
    if (videoRef.current) {
      videoRef.current.playbackRate = rate
    }
  }, [])

  // 同一集重複推入（例：從 MiniPlayer 點回播放頁，PlayerRoute 重新 fetch 拿到
  // 新的簽章 audioUrl）時保留舊物件 — 換 <audio src> 會觸發 reload 把播放中斷。
  const setCurrentEpisode = useCallback((episode: Episode | null) => {
    setCurrentEpisodeState(prev => (prev && episode && prev.id === episode.id ? prev : episode))
  }, [])

  const loadProgress = useCallback((episodeId: string) => {
    if (currentEpisodeIdRef.current !== episodeId) {
      lastSavedTimeRef.current = 0
    }
    currentEpisodeIdRef.current = episodeId
    const saved = storageGet<SavedProgress>(LS_KEY_CURRENT_TIME)
    if (saved && saved.episodeId === episodeId && saved.currentTime > 0) {
      return { currentTime: saved.currentTime, exists: true }
    }
    // 本機沒有進度快取（例如換裝置登入）：退回用 ActivityProvider 從後端
    // GET /activity 抓回來的 lastPlayed，讓播放進度真正跨裝置同步。
    if (lastPlayedEpisodeId === episodeId && lastPlayedPosition !== null && lastPlayedPosition > 0) {
      return { currentTime: lastPlayedPosition, exists: true }
    }
    return { currentTime: 0, exists: false }
  }, [lastPlayedEpisodeId, lastPlayedPosition])

  const value: PlayerContextValue = {
    currentTime, isPlaying, duration, playbackRate, videoRef, currentEpisode,
    seekTo, setVideoRef, play, pause, setPlaybackRate, loadProgress, setCurrentEpisode,
  }

  return (
    <PlayerContext.Provider value={value}>
      {children}
    </PlayerContext.Provider>
  )
}
