import { useEffect, useRef } from 'react'
import type { DailyOrder } from '../api'
import type { SegmentLoadState } from '../state/useSegmentPlayer'
import type { Episode } from '../types/episode'

export interface UseEpisodeProgressParams {
  readonly episode: Episode | null
  readonly currentTime: number
  readonly duration: number
  readonly loadState: SegmentLoadState
  /** 全域 PlayerProvider 目前正在播的集數；用來判斷是不是「冷啟動」續播。 */
  readonly currentEpisode: Episode | null
  seekTo(time: number): void
  loadProgress(episodeId: string): { readonly currentTime: number; readonly exists: boolean }
  markListened(episodeId: string): void
  addListenMinutes(month: string, minutes: number): void
  markPlayed(date: string): Promise<DailyOrder | null>
}

/** 續播定位 + 80%/90% 完聽節流標記。
 *
 * 三件事都以「同一集只做一次」為前提，靠 ref（而非 state）記帳，避免額外 re-render。 */
export function useEpisodeProgress({
  episode, currentTime, duration, loadState, currentEpisode,
  seekTo, loadProgress, markListened, addListenMinutes, markPlayed,
}: UseEpisodeProgressParams): void {
  const episodeIdRef = useRef<string | null>(null)
  const initialSeekAppliedRef = useRef(false)
  const hasMarkedListenedRef = useRef(false)
  const hasMarkedDailyPlayedRef = useRef(false)

  useEffect(() => {
    if (episode && episode.id !== episodeIdRef.current) {
      episodeIdRef.current = episode.id
      initialSeekAppliedRef.current = false
      hasMarkedListenedRef.current = false
    }
  }, [episode])

  useEffect(() => {
    if (!episode || initialSeekAppliedRef.current) return
    const episodeId = episode.id
    // 全域 PlayerProvider 已在播這集（例：從 MiniPlayer 點回播放頁）→ currentTime
    // 才是事實。localStorage 進度是節流快照，會落後幾百毫秒到 1 秒，
    // 拿它去 seek 就是使用者看到的「點進來倒退一下」。只有冷啟動才需要續播定位。
    if (currentEpisode?.id === episodeId && currentTime > 0) {
      initialSeekAppliedRef.current = true
      loadProgress(episodeId) // 副作用：綁定 provider 的 currentEpisodeIdRef，續存進度
      return
    }
    const progress = loadProgress(episodeId)
    if (!progress.exists) return

    // 等 hook loadState === 'ready'（segments decode 完成）才能 seek，否則會定位到 0。
    if (loadState !== 'ready') return
    initialSeekAppliedRef.current = true
    seekTo(progress.currentTime)
  }, [episode, currentEpisode, currentTime, loadProgress, loadState, seekTo])

  useEffect(() => {
    if (!episode || duration <= 0 || hasMarkedListenedRef.current) return
    if (currentTime / duration > 0.8) {
      hasMarkedListenedRef.current = true
      markListened(episode.id)
      const ymMin = new Date().toLocaleDateString('en-CA').slice(0, 7)
      addListenMinutes(ymMin, Math.floor(currentTime / 60))
    }
  }, [currentTime, duration, episode, markListened, addListenMinutes])

  useEffect(() => {
    if (!episode || duration <= 0 || hasMarkedDailyPlayedRef.current) return
    if (currentTime / duration >= 0.9) {
      hasMarkedDailyPlayedRef.current = true
      void markPlayed(new Date().toLocaleDateString('en-CA'))
    }
  }, [currentTime, duration, episode, markPlayed])
}
