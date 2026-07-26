import { createContext, type RefObject } from 'react'
import type { Episode } from '../types/episode'

export type PlayerContextValue = {
  readonly currentTime: number
  readonly isPlaying: boolean
  readonly duration: number
  readonly playbackRate: number
  readonly videoRef: RefObject<HTMLMediaElement | null>
  readonly currentEpisode: Episode | null
  seekTo(time: number): void
  setVideoRef(el: HTMLMediaElement | null): void
  play(): void
  pause(): void
  setPlaybackRate(rate: number): void
  loadProgress(episodeId: string): { readonly currentTime: number; readonly exists: boolean }
  setCurrentEpisode(episode: Episode | null): void
}

export const PlayerContext = createContext<PlayerContextValue | null>(null)
