import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, type ChannelPublic } from '../api'
import {
  ChannelSubscriptionsContext,
  type ChannelSubscriptionsContextValue,
} from './channelSubscriptionsContextValue'

export function ChannelSubscriptionsProvider({ children }: { readonly children: ReactNode }) {
  const [subscribed, setSubscribed] = useState<ReadonlyMap<string, ChannelPublic>>(new Map())

  useEffect(() => {
    api
      .listMySubscriptions()
      .then(channels => setSubscribed(new Map(channels.map(c => [c.slug, c]))))
      .catch(err => {
        console.warn('[channel-subscriptions] initial load failed', err)
      })
  }, [])

  const toggle = useCallback(async (channel: ChannelPublic) => {
    // willAdd 從當下的 subscribed 狀態直接算，不能靠 setState updater 內的
    // side-effect 變數——updater 何時真的被呼叫由 React 決定，不保證發生在
    // 下一行讀到 willAdd 之前（曾實測炸過：optimistic UI 對了，但打錯 API）。
    const willAdd = !subscribed.has(channel.slug)
    setSubscribed(prev => {
      const next = new Map(prev)
      if (willAdd) {
        next.set(channel.slug, channel)
      } else {
        next.delete(channel.slug)
      }
      return next
    })
    const call = willAdd ? api.subscribeChannel(channel.slug) : api.unsubscribeChannel(channel.slug)
    await call.catch(err => console.warn('[channel-subscriptions] toggle sync failed', err))
  }, [subscribed])

  const has = useCallback((slug: string) => subscribed.has(slug), [subscribed])

  const value: ChannelSubscriptionsContextValue = { subscribed, toggle, has }

  return (
    <ChannelSubscriptionsContext.Provider value={value}>
      {children}
    </ChannelSubscriptionsContext.Provider>
  )
}
