import { createContext } from 'react'
import type { Episode } from '../types/episode'
import type { SegmentLoadState, SegmentPlayer } from './useSegmentPlayer'

/** Provider 對外暴露：狀態鏡像 + hook 方法轉發。hook 實體只在 Provider 內一份。 */
export type PlayerContextValue = {
  readonly currentTime: number
  readonly isPlaying: boolean
  readonly duration: number
  readonly playbackRate: number
  readonly muted: boolean
  readonly loadState: SegmentLoadState
  readonly currentEpisode: Episode | null
  seekTo(time: number): void
  /** 練習模式 word click：跳到 (cue.start + word.start)。回 false 表示資料不足
   *  （沒 word boundary 或 cue / word index 超出範圍），LyricsView 走查詞 fallback。 */
  seekToWord(cueIdx: number, wordIdx: number): boolean
  play(): void
  pause(): void
  loadEpisode(episode: Episode | null): void
  setPlaybackRate(rate: number): void
  setMuted(m: boolean): void
  loadProgress(episodeId: string): { readonly currentTime: number; readonly exists: boolean }
  setCurrentEpisode(episode: Episode | null): void
  /** 單字抽樣（給 PronounceButton / WordCardPanel / ReplayAudioButton 用）。 */
  playSegment(segmentIdx: number, offsetSec: number, durationSec: number): void
  /** 對外 hook 暴露（給元件直接呼叫 decode / seek 等進階操作）。 */
  getSegmentPlayer(): SegmentPlayer
}

export const PlayerContext = createContext<PlayerContextValue | null>(null)
