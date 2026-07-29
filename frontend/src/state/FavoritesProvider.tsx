import { useCallback, useEffect, useState, type ReactNode } from 'react'
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
    await call.catch(err => console.warn('[favorites] toggle sync failed', err))
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
