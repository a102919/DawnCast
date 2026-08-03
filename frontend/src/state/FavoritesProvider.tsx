import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { toast } from 'sonner'
import { api } from '../api'
import { FavoritesContext, type FavoritesContextValue } from './favoritesContextValue'

export function FavoritesProvider({ children }: { readonly children: ReactNode }) {
  const [favorites, setFavorites] = useState<ReadonlySet<string>>(new Set())

  useEffect(() => {
    api.getFavorites().then(ids => setFavorites(new Set(ids))).catch(err => {
      console.warn('[favorites] initial load failed', err)
    })
  }, [])

  const toggle = useCallback(async (id: string) => {
    // willAdd 從當下的 favorites 狀態直接算，不能靠 setState updater 內的
    // side-effect 變數——updater 何時真的被呼叫由 React 決定，不保證發生在
    // 下一行讀到 willAdd 之前（實測過會導致 optimistic UI 對了但打錯 API）。
    const willAdd = !favorites.has(id)
    setFavorites(prev => {
      const next = new Set(prev)
      if (willAdd) {
        next.add(id)
      } else {
        next.delete(id)
      }
      return next
    })
    const call = willAdd ? api.addFavorite(id) : api.removeFavorite(id)
    await call.catch(err => {
      console.warn('[favorites] toggle sync failed', err)
      // 失敗要把樂觀更新復原，否則 UI 顯示已收藏／已取消收藏但後端沒真的變，
      // 使用者無從察覺、下次重整後又「消失」（見 ChannelSubscriptionsProvider 同模式）。
      setFavorites(prev => {
        const next = new Set(prev)
        if (willAdd) {
          next.delete(id)
        } else {
          next.add(id)
        }
        return next
      })
      toast.error(willAdd ? '加入收藏失敗，請稍後再試' : '取消收藏失敗，請稍後再試')
    })
  }, [favorites])

  const has = useCallback(
    (id: string) => favorites.has(id),
    [favorites],
  )

  const value: FavoritesContextValue = { favorites, toggle, has }

  return (
    <FavoritesContext.Provider value={value}>
      {children}
    </FavoritesContext.Provider>
  )
}
