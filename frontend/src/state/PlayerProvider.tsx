import { useCallback, useRef, useState, type ReactNode } from 'react'
import { storageGet, storageSet } from '../lib/storage'
import { PlayerContext, type PlayerContextValue } from './playerContextValue'

const LS_KEY_CURRENT_TIME = 'dawncast:player:currentTime'
const LS_KEY_LAST_EPISODE_ID = 'dawncast:player:lastEpisodeId'
const PROGRESS_THROTTLE_MS = 200

type SavedProgress = {
  readonly episodeId: string
  readonly currentTime: number
}

export function PlayerProvider({ children }: { readonly children: ReactNode }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRateState] = useState(1)
  const progressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastSavedTimeRef = useRef<number>(0)
  const currentEpisodeIdRef = useRef<string | null>(null)

  const persistProgress = useCallback((time: number, episodeId: string | null) => {
    if (!episodeId) return
    if (Math.abs(time - lastSavedTimeRef.current) < 0.5) return
    lastSavedTimeRef.current = time
    storageSet<SavedProgress>(LS_KEY_CURRENT_TIME, { episodeId, currentTime: time })
    storageSet<string>(LS_KEY_LAST_EPISODE_ID, episodeId)
  }, [])

  const setVideoRef = useCallback((el: HTMLVideoElement | null) => {
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
      el.onpause = () => setIsPlaying(false)
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
    return { currentTime: 0, exists: false }
  }, [])

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
