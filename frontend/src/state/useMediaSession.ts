import { useEffect } from 'react'
import type { Episode } from '../types/episode'

const DEFAULT_SEEK_OFFSET_SEC = 10

export interface UseMediaSessionParams {
  readonly episode: Episode | null
  readonly isPlaying: boolean
  getCurrentTime(): number
  play(): void
  pause(): void
  seekTo(time: number): void
}

/** Media Session：登記成系統認得的「正在播放音訊」，背景切走 / 鎖屏才不會被
 *  OS 中止播放，同時給鎖屏/耳機實體鍵播放控制。純 Web Audio（無 <audio> 元素）
 *  沒有這層會被 iOS Safari 當成一般背景分頁處理，切出去很快就把 AudioContext 停掉。 */
export function useMediaSession({ episode, isPlaying, getCurrentTime, play, pause, seekTo }: UseMediaSessionParams): void {
  useEffect(() => {
    if (!('mediaSession' in navigator)) return
    navigator.mediaSession.metadata = episode
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
    if (!('mediaSession' in navigator)) return
    navigator.mediaSession.playbackState = episode ? (isPlaying ? 'playing' : 'paused') : 'none'
  }, [episode, isPlaying])

  useEffect(() => {
    if (!('mediaSession' in navigator)) return
    const ms = navigator.mediaSession
    ms.setActionHandler('play', () => { play() })
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
}
