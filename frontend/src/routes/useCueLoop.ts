import { useCallback, useEffect, useRef, useState } from 'react'
import type { Cue, Episode } from '../types/episode'

export interface UseCueLoopResult {
  readonly loopCueIdx: number | null
  readonly isCueLooping: boolean
  readonly canLoopCue: boolean
  /** 開啟/關閉單句循環（從目前 active cue 起點開始）。 */
  toggle(): void
  /** 下一句；循環中時同步把 lock 目標移到下一句。 */
  next(): void
  /** 循環中時把 lock 目標移到指定 cue（點字幕行 / 詞卡「重聽這句」都會跳到別句）。 */
  retarget(cue: Cue): void
}

export interface UseCueLoopParams {
  readonly episode: Episode | null
  readonly currentTime: number
  readonly activeCueIdx: number
  seekTo(time: number): void
  play(): Promise<void>
  playWithUnlock(): void
}

/** 單句循環狀態機：越過 lock 住的 cue 尾端就自動 seek 回起點續播。 */
export function useCueLoop({ episode, currentTime, activeCueIdx, seekTo, play, playWithUnlock }: UseCueLoopParams): UseCueLoopResult {
  const [loopCueIdx, setLoopCueIdx] = useState<number | null>(null)
  const episodeIdRef = useRef<string | null>(null)

  // 換集數時清掉舊的循環鎖定，避免指向已經不存在的 cue index。
  useEffect(() => {
    if (episode && episode.id !== episodeIdRef.current) {
      episodeIdRef.current = episode.id
      setLoopCueIdx(null)
    }
  }, [episode])

  useEffect(() => {
    if (!episode || loopCueIdx === null) return
    const cue = episode.cues[loopCueIdx]
    if (!cue || (currentTime >= cue.start && currentTime < cue.end)) return
    seekTo(cue.start)
    play()
  }, [currentTime, episode, loopCueIdx, play, seekTo])

  const toggle = useCallback(() => {
    if (loopCueIdx !== null) {
      setLoopCueIdx(null)
      return
    }
    if (!episode || activeCueIdx < 0) return
    const cue = episode.cues[activeCueIdx]
    if (!cue) return
    setLoopCueIdx(activeCueIdx)
    seekTo(cue.start)
    playWithUnlock()
  }, [activeCueIdx, episode, loopCueIdx, playWithUnlock, seekTo])

  const next = useCallback(() => {
    if (!episode) return
    const nextCueIdx = activeCueIdx + 1
    const nextCue = episode.cues[nextCueIdx]
    if (!nextCue) return
    if (loopCueIdx !== null) setLoopCueIdx(nextCueIdx)
    seekTo(nextCue.start)
  }, [activeCueIdx, episode, loopCueIdx, seekTo])

  const retarget = useCallback((cue: Cue) => {
    if (loopCueIdx === null || !episode) return
    const cueIdx = episode.cues.indexOf(cue)
    if (cueIdx >= 0) setLoopCueIdx(cueIdx)
  }, [episode, loopCueIdx])

  return {
    loopCueIdx,
    isCueLooping: loopCueIdx !== null,
    canLoopCue: loopCueIdx !== null || activeCueIdx >= 0,
    toggle,
    next,
    retarget,
  }
}
