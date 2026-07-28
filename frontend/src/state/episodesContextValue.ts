import { createContext } from 'react'
import type { MockEpisode } from '../lib'

export type EpisodesContextValue = {
  readonly episodes: readonly MockEpisode[]
  readonly loading: boolean
  readonly error: string | null
  /** 重新抓取集數清單，供載入失敗時的重試按鈕使用。 */
  refresh(): Promise<void>
}

export const EpisodesContext = createContext<EpisodesContextValue | null>(null)
