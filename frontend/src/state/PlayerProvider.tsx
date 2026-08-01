import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { storageGet, storageSet } from '../lib/storage'
import { PlayerContext, type PlayerContextValue } from './playerContextValue'
import { useSegmentPlayer } from './useSegmentPlayer'
import { useMediaSession } from './useMediaSession'
import type { Episode } from '../types/episode'

const LS_KEY_CURRENT_TIME = 'dawncast:player:currentTime'
const LS_KEY_LAST_EPISODE_ID = 'dawncast:player:lastEpisodeId'
const PROGRESS_THROTTLE_MS = 200

type SavedProgress = {
  readonly episodeId: string
  readonly currentTime: number
}

export interface PlayerProviderProps {
  readonly children: ReactNode
  /** activity.lastPlayed* 由外層（ActivityProvider 底下的 App shell）注入，
   *  Provider 本身不直接依賴 useActivity，避免跨 Provider 隱性耦合。 */
  readonly lastPlayedEpisodeId: string | null
  readonly lastPlayedPosition: number | null
  setLastPlayed(episodeId: string, position: number, opts?: { readonly force?: boolean }): void
}

/** Provider 包 useSegmentPlayer + 進度持久化 + lastPlayed 雲端同步。
 *
 * hook 內已管單一 <audio> 引擎（見 audioEngine.ts），這層只剩 React 狀態鏡像 +
 * 跨分頁存檔 flush + activity.lastPlayed 同步。保留 currentEpisode state 讓
 * MiniPlayer / GlobalAudioHost / PlayerRoute 都能讀。 */
export function PlayerProvider({ children, lastPlayedEpisodeId, lastPlayedPosition, setLastPlayed }: PlayerProviderProps) {
  const player = useSegmentPlayer()
  // useSegmentPlayer() 每次 render 回傳新物件字面量；player 本身拿來當 useCallback
  // 依賴會讓 setCurrentEpisode/seekTo 每次 render 都變新函式 → 依賴它們的 useEffect
  // 跟著每次重跑 → loadEpisode 重跑 → setState → 再 re-render → 無窮迴圈（實測會卡死
  // 在反覆重抓前 5 段 segments，播放永遠打不進 ready）。用 ref 存最新 player，讓外露的
  // callback 保持穩定 identity，只在真正呼叫當下才讀最新方法。
  const playerRef = useRef(player)
  useLayoutEffect(() => { playerRef.current = player })
  const [currentEpisode, setCurrentEpisodeState] = useState<Episode | null>(null)
  const currentEpisodeIdRef = useRef<string | null>(null)
  const lastSavedTimeRef = useRef<number>(0)
  const lastFlushAtRef = useRef<number>(0)

  const persistProgress = useCallback((time: number, episodeId: string | null, opts?: { readonly force?: boolean }) => {
    if (!episodeId) return
    if (!opts?.force && Math.abs(time - lastSavedTimeRef.current) < 0.5) return
    lastSavedTimeRef.current = time
    storageSet<SavedProgress>(LS_KEY_CURRENT_TIME, { episodeId, currentTime: time })
    storageSet<string>(LS_KEY_LAST_EPISODE_ID, episodeId)
    setLastPlayed(episodeId, time, opts)
  }, [setLastPlayed])

  const setCurrentEpisode = useCallback((episode: Episode | null) => {
    // 同集重推（例：首頁→再點回同一集 / PlayerRoute 重 fetch 拿到新物件參考）：
    // 只認 id，不認物件參考。id 相同就是「同一集」，跳過 loadEpisode 避免打斷正在播放的音訊
    // （loadEpisode 會把 currentTime 砍回 0 並重設引擎 src）；換到不同集才需要真的重載。
    const isSameEpisode = episode !== null && episode.id === currentEpisodeIdRef.current
    setCurrentEpisodeState(prev => (prev && episode && prev.id === episode.id ? prev : episode))
    currentEpisodeIdRef.current = episode?.id ?? null
    if (isSameEpisode) return
    playerRef.current.loadEpisode(episode)
  }, [])

  // 進度節流寫 localStorage + activity（每 200ms）
  useEffect(() => {
    if (player.loadState !== 'ready') return
    const t = player.currentTime
    const epId = currentEpisodeIdRef.current
    const now = Date.now()
    if (now - lastFlushAtRef.current < PROGRESS_THROTTLE_MS) return
    lastFlushAtRef.current = now
    persistProgress(t, epId)
  }, [player.currentTime, player.loadState, persistProgress])

  // 換分頁 / 關閉分頁前強制 flush
  useEffect(() => {
    const flush = () => {
      if (currentEpisodeIdRef.current) {
        persistProgress(player.currentTime, currentEpisodeIdRef.current, { force: true })
      }
    }
    document.addEventListener('visibilitychange', flush)
    window.addEventListener('pagehide', flush)
    return () => {
      document.removeEventListener('visibilitychange', flush)
      window.removeEventListener('pagehide', flush)
    }
  }, [persistProgress, player.currentTime])

  const loadProgress = useCallback((episodeId: string) => {
    if (currentEpisodeIdRef.current !== episodeId) {
      lastSavedTimeRef.current = 0
    }
    currentEpisodeIdRef.current = episodeId
    const saved = storageGet<SavedProgress>(LS_KEY_CURRENT_TIME)
    if (saved && saved.episodeId === episodeId && saved.currentTime > 0) {
      return { currentTime: saved.currentTime, exists: true }
    }
    if (lastPlayedEpisodeId === episodeId && lastPlayedPosition !== null && lastPlayedPosition > 0) {
      return { currentTime: lastPlayedPosition, exists: true }
    }
    return { currentTime: 0, exists: false }
  }, [lastPlayedEpisodeId, lastPlayedPosition])

  const seekTo = useCallback((time: number) => {
    if (!Number.isFinite(time)) return
    playerRef.current.seekTo(time)
  }, [])
  const seekToWord = useCallback((cueIdx: number, wordIdx: number): boolean => {
    return playerRef.current.seekToWord(cueIdx, wordIdx)
  }, [])

  const play = useCallback(() => { playerRef.current.play() }, [])
  const pause = useCallback(() => { playerRef.current.pause() }, [])
  const loadEpisode = useCallback((ep: Episode | null) => {
    playerRef.current.loadEpisode(ep)
  }, [])
  const playClip = useCallback((startSec: number, durationSec: number) => {
    playerRef.current.playClip(startSec, durationSec)
  }, [])
  const getCurrentTime = useCallback(() => playerRef.current.currentTime, [])

  useMediaSession({
    episode: currentEpisode,
    isPlaying: player.isPlaying,
    currentTime: player.currentTime,
    duration: player.duration,
    playbackRate: player.playbackRate,
    getCurrentTime,
    play,
    pause,
    seekTo,
  })

  const value: PlayerContextValue = {
    currentTime: player.currentTime,
    isPlaying: player.isPlaying,
    duration: player.duration,
    playbackRate: player.playbackRate,
    muted: player.muted,
    loadState: player.loadState,
    currentEpisode,
    seekTo,
    seekToWord,
    play,
    pause,
    loadEpisode,
    setPlaybackRate: player.setPlaybackRate,
    setMuted: player.setMuted,
    loadProgress,
    setCurrentEpisode,
    playClip,
  }

  return (
    <PlayerContext.Provider value={value}>
      {children}
    </PlayerContext.Provider>
  )
}
