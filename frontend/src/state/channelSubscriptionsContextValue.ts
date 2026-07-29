import { createContext } from 'react'
import type { ChannelPublic } from '../api'

export type ChannelSubscriptionsContextValue = {
  /** key = 頻道 slug。用 Map 而非 Set：首頁「你追蹤的頻道」需要封面/名稱，
   *  直接拿得到，不用再拿 slug 反查一次 listChannels()。 */
  readonly subscribed: ReadonlyMap<string, ChannelPublic>
  toggle(channel: ChannelPublic): Promise<void>
  has(slug: string): boolean
}

export const ChannelSubscriptionsContext = createContext<ChannelSubscriptionsContextValue | null>(
  null,
)
