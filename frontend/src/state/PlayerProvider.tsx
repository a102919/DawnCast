import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { storageGet, storageSet } from '../lib/storage'
import { PlayerContext, type PlayerContextValue } from './playerContextValue'
import { useActivity } from './useActivity'

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
  const progressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
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

  const setVideoRef = useCallback((el: HTMLMediaElement | null) => {
    if (videoRef.current && progressTimerRef.current) {
      clearTimeout(progressTimerRef.current)
      progressTimerRef.current = null
    }
    videoRef.current = el
    if (el) {
      el.ontimeupdate = () => {
        const t = el.currentTime
        setCurrentTime(t)
        if (progressTimerRef.current) clearTimeout(progressTimerRef.current)
        progressTimerRef.current = setTimeout(() => {
          persistProgress(t, currentEpisodeIdRef.current)
        }, PROGRESS_THROTTLE_MS)
      }
      el.onplay = () => setIsPlaying(true)
      el.onpause = () => {
        setIsPlaying(false)
        persistProgress(el.currentTime, currentEpisodeIdRef.current, { force: true })
      }
      el.onloadedmetadata = () => setDuration(el.duration)
    }
  }, [persistProgress])

  const seekTo = useCallback((time: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = time
      persistProgress(time, currentEpisodeIdRef.current)
      setCurrentTime(time)
    }
  }, [persistProgress])

  const play = useCallback(() => {
    videoRef.current?.play()
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

  const loadProgress = useCallback((episodeId: string) => {
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
    currentTime, isPlaying, duration, playbackRate, videoRef,
    seekTo, setVideoRef, play, pause, setPlaybackRate, loadProgress,
  }

  return (
    <PlayerContext.Provider value={value}>
      {children}
    </PlayerContext.Provider>
  )
}
