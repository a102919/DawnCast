import { createContextHook } from './createContextHook'
import {
  ChannelSubscriptionsContext,
  type ChannelSubscriptionsContextValue,
} from './channelSubscriptionsContextValue'

export const useChannelSubscriptions: () => ChannelSubscriptionsContextValue = createContextHook(
  ChannelSubscriptionsContext,
  'useChannelSubscriptions',
  'ChannelSubscriptionsProvider',
)
