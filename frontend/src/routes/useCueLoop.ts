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
  readonly isPlaying: boolean
  seekTo(time: number): void
  play(): void
  playWithUnlock(): void
}

/** 單句循環狀態機：越過 lock 住的 cue 尾端就自動 seek 回起點續播。 */
export function useCueLoop({ episode, currentTime, activeCueIdx, isPlaying, seekTo, play, playWithUnlock }: UseCueLoopParams): UseCueLoopResult {
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
    // 只認「越過尾端」這一個回捲條件。原本寫成「不在 [start, end) 區間內就回捲」，
    // 把「currentTime 還沒追上 start」也算進去了——seek 完到下一幀位置更新之間本來就
    // 會短暫落在區間前面，只要位置回報偏低一點，這個 effect 就會每幀重跑一次
    // seek + play，同一句被無限重啟，聽起來就是卡住一直發同一個聲音。
    if (!cue || currentTime < cue.end) return
    // 回捲是「播放中」才存在的行為。少了這個 guard，暫停的 click 若剛好插在
    // 「rAF 把 currentTime 推過 cue.end」與這個 effect flush 之間，effect 會在
    // pause() 之後才跑，無條件 seek + play 把使用者的暫停直接蓋掉——循環還鎖著，
    // 於是那句 cue（尤其是唸單字的短句）被無限重播，聽起來像卡住一直發同一個聲音。
    if (!isPlaying) return
    seekTo(cue.start)
    play()
  }, [currentTime, episode, isPlaying, loopCueIdx, play, seekTo])

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
