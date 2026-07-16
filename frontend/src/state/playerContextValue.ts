import { createContext, type RefObject } from 'react'

export type PlayerContextValue = {
  readonly currentTime: number
  readonly isPlaying: boolean
  readonly duration: number
  readonly playbackRate: number
  readonly videoRef: RefObject<HTMLVideoElement | null>
  seekTo(time: number): void
  setVideoRef(el: HTMLVideoElement | null): void
  play(): void
  pause(): void
  setPlaybackRate(rate: number): void
  loadProgress(episodeId: string): { readonly currentTime: number; readonly exists: boolean }
}

export const PlayerContext = createContext<PlayerContextValue | null>(null)
