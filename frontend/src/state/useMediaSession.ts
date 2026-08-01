import { useEffect, useRef } from 'react'
import type { Episode } from '../types/episode'

const DEFAULT_SEEK_OFFSET_SEC = 10

export interface UseMediaSessionParams {
  readonly episode: Episode | null
  readonly isPlaying: boolean
  readonly currentTime: number
  readonly duration: number
  readonly playbackRate: number
  getCurrentTime(): number
  play(): void
  pause(): void
  seekTo(time: number): void
}

function getMs(): MediaSession | null {
  if (typeof navigator === 'undefined') return null
  return 'mediaSession' in navigator ? navigator.mediaSession : null
}

/** 推整集 episode-wide 進度給 OS（iOS Now Playing / Android MediaSession）。
 *  沒這層，鎖定畫面直接讀 active <audio> 的 segment-local duration（1–3 秒），
 *  顯示 0:02 / -0:01。站內用 React state 每 100ms 更新，native API 改用整秒 key
 *  去重：每秒、換集、duration/rate/playing 變化時重推一次，避免每 100ms 敲 bridge。
 *  數值 clamp + try/catch 守住 Safari InvalidStateError（duration 0、position 越界、
 *  playbackRate <= 0 都會丟）。 */
function usePositionState({
  episode, isPlaying, currentTime, duration, playbackRate,
}: Pick<UseMediaSessionParams, 'episode' | 'isPlaying' | 'currentTime' | 'duration' | 'playbackRate'>): void {
  const lastKeyRef = useRef<string | null>(null)
  useEffect(() => {
    const ms = getMs()
    if (!ms || !('setPositionState' in ms)) return

    if (!episode) {
      try { ms.setPositionState() } catch { /* old Safari: ignore */ }
      lastKeyRef.current = null
      return
    }

    if (!Number.isFinite(duration) || duration <= 0) return

    const safeRate = Number.isFinite(playbackRate) && playbackRate > 0 ? playbackRate : 1
    const raw = Number.isFinite(currentTime) ? currentTime : 0
    const pos = Math.max(0, Math.min(raw, duration))
    const key = `${episode.id}|${Math.floor(pos)}|${duration}|${safeRate}|${isPlaying ? 1 : 0}`
    if (key === lastKeyRef.current) return

    try {
      ms.setPositionState({ duration, position: pos, playbackRate: safeRate })
      // 只在呼叫成功後 commit dedup key；拋錯則保持 ref 為舊值，下一次 render 會重推。
      lastKeyRef.current = key
    } catch {
      // InvalidStateError 等：ref 不更新，下次有效 state 一定會再送一次。
    }
  }, [episode, isPlaying, currentTime, duration, playbackRate])
}

/** Media Session：登記成系統認得的「正在播放音訊」，背景切走 / 鎖屏才不會被
 *  OS 中止播放，同時給鎖屏/耳機實體鍵播放控制。純 Web Audio（無 <audio> 元素）
 *  沒有這層會被 iOS Safari 當成一般背景分頁處理，切出去很快就把 AudioContext 停掉。 */
export function useMediaSession({
  episode,
  isPlaying,
  currentTime,
  duration,
  playbackRate,
  getCurrentTime,
  play,
  pause,
  seekTo,
}: UseMediaSessionParams): void {
  useEffect(() => {
    const ms = getMs()
    if (!ms) return
    ms.metadata = episode
      ? new MediaMetadata({
        title: episode.title,
        artist: 'DawnCast',
        artwork: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
      })
      : null
  }, [episode])

  useEffect(() => {
    const ms = getMs()
    if (!ms) return
    ms.playbackState = episode ? (isPlaying ? 'playing' : 'paused') : 'none'
  }, [episode, isPlaying])

  useEffect(() => {
    const ms = getMs()
    if (!ms) return
    ms.setActionHandler('play', () => play())
    ms.setActionHandler('pause', () => pause())
    ms.setActionHandler('seekto', details => {
      if (details.seekTime !== undefined) seekTo(details.seekTime)
    })
    ms.setActionHandler('seekbackward', details => {
      seekTo(Math.max(0, getCurrentTime() - (details.seekOffset ?? DEFAULT_SEEK_OFFSET_SEC)))
    })
    ms.setActionHandler('seekforward', details => {
      seekTo(getCurrentTime() + (details.seekOffset ?? DEFAULT_SEEK_OFFSET_SEC))
    })
    return () => {
      ms.setActionHandler('play', null)
      ms.setActionHandler('pause', null)
      ms.setActionHandler('seekto', null)
      ms.setActionHandler('seekbackward', null)
      ms.setActionHandler('seekforward', null)
    }
  }, [play, pause, seekTo, getCurrentTime])

  usePositionState({ episode, isPlaying, currentTime, duration, playbackRate })
}
