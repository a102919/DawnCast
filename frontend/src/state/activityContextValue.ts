import { createContext } from 'react'

export type ActivityContextValue = {
  readonly streakDates: readonly string[]
  readonly listenMinutes: Readonly<Record<string, number>>
  readonly lookupCount: Readonly<Record<string, number>>
  readonly listenedEpisodeIds: ReadonlySet<string>
  readonly lastPlayedEpisodeId: string | null
  readonly lastPlayedPosition: number | null
  /** 標記某集已聽完（>=80%）：同步加入今日 streak 日期 + 已聽集數。 */
  markListened(episodeId: string): void
  /** 指定月份「加上」聆聽分鐘數（增量，非取代）。 */
  addListenMinutes(month: string, minutes: number): void
  /** 指定月份「加上」查詞次數（增量，非取代）。 */
  addLookupCount(month: string, count: number): void
  /** 播放進度快照。預設節流送出 PATCH；force=true（pause/pagehide）時立即送出。 */
  setLastPlayed(episodeId: string, position: number, opts?: { readonly force?: boolean }): void
}

export const ActivityContext = createContext<ActivityContextValue | null>(null)
