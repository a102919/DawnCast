import { useContext } from 'react'
import {
  ChannelSubscriptionsContext,
  type ChannelSubscriptionsContextValue,
} from './channelSubscriptionsContextValue'

export function useChannelSubscriptions(): ChannelSubscriptionsContextValue {
  const ctx = useContext(ChannelSubscriptionsContext)
  if (!ctx) throw new Error('useChannelSubscriptions must be used inside ChannelSubscriptionsProvider')
  return ctx
}
