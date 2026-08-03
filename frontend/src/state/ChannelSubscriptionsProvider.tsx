import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { api, type ChannelPublic } from '../api'
import {
  ChannelSubscriptionsContext,
  type ChannelSubscriptionsContextValue,
} from './channelSubscriptionsContextValue'
import { useOptimisticToggle } from './useOptimisticToggle'

export function ChannelSubscriptionsProvider({ children }: { readonly children: ReactNode }) {
  const [subscribed, setSubscribed] = useState<ReadonlyMap<string, ChannelPublic>>(new Map())
  const runToggle = useOptimisticToggle<string>('channel-subscriptions')

  useEffect(() => {
    api
      .listMySubscriptions()
      .then(channels => setSubscribed(new Map(channels.map(c => [c.slug, c]))))
      .catch(err => {
        console.warn('[channel-subscriptions] initial load failed', err)
      })
  }, [])

  const toggle = useCallback(async (channel: ChannelPublic) => {
    const slug = channel.slug
    // willAdd 從當下的 subscribed 狀態直接算，不能靠 setState updater 內的
    // side-effect 變數——updater 何時真的被呼叫由 React 決定，不保證發生在
    // 下一行讀到 willAdd 之前（曾實測炸過：optimistic UI 對了，但打錯 API）。
    const willAdd = !subscribed.has(slug)
    setSubscribed(prev => {
      const next = new Map(prev)
      if (willAdd) {
        next.set(slug, channel)
      } else {
        next.delete(slug)
      }
      return next
    })

    await runToggle(
      slug,
      willAdd,
      desired => (desired ? api.subscribeChannel(slug) : api.unsubscribeChannel(slug)),
      desired => {
        setSubscribed(prev => {
          const next = new Map(prev)
          if (desired) {
            next.delete(slug)
          } else {
            next.set(slug, channel)
          }
          return next
        })
        toast.error(desired ? '追蹤失敗，請稍後再試' : '取消追蹤失敗，請稍後再試')
      },
    )
  }, [subscribed, runToggle])

  const has = useCallback((slug: string) => subscribed.has(slug), [subscribed])

  const value = useMemo<ChannelSubscriptionsContextValue>(
    () => ({ subscribed, toggle, has }),
    [subscribed, toggle, has],
  )

  return (
    <ChannelSubscriptionsContext.Provider value={value}>
      {children}
    </ChannelSubscriptionsContext.Provider>
  )
}
