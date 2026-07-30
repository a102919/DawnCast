import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { api, type ChannelPublic } from '../api'
import {
  ChannelSubscriptionsContext,
  type ChannelSubscriptionsContextValue,
} from './channelSubscriptionsContextValue'

export function ChannelSubscriptionsProvider({ children }: { readonly children: ReactNode }) {
  const [subscribed, setSubscribed] = useState<ReadonlyMap<string, ChannelPublic>>(new Map())
  // 每個 slug 同一時間只送一個請求；連點時只更新這裡記錄的「最新想要的狀態」，
  // 讓飛行中的請求結束後自己判斷要不要再送一次，而不是讓兩個請求互相競速。
  const pendingRef = useRef<Map<string, boolean>>(new Map())

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

    if (pendingRef.current.has(slug)) {
      pendingRef.current.set(slug, willAdd)
      return
    }
    pendingRef.current.set(slug, willAdd)

    let desired = willAdd
    for (;;) {
      try {
        await (desired ? api.subscribeChannel(slug) : api.unsubscribeChannel(slug))
      } catch (err) {
        console.warn('[channel-subscriptions] toggle sync failed', err)
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
        break
      }
      const latest = pendingRef.current.get(slug)
      if (latest === desired) break
      desired = latest as boolean
    }
    pendingRef.current.delete(slug)
  }, [subscribed])

  const has = useCallback((slug: string) => subscribed.has(slug), [subscribed])

  const value: ChannelSubscriptionsContextValue = { subscribed, toggle, has }

  return (
    <ChannelSubscriptionsContext.Provider value={value}>
      {children}
    </ChannelSubscriptionsContext.Provider>
  )
}
