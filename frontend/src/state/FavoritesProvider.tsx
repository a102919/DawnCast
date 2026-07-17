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
    let willAdd = false
    setFavorites(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
        willAdd = true
      }
      return next
    })
    const call = willAdd ? api.addFavorite(id) : api.removeFavorite(id)
    await call.catch(err => console.warn('[favorites] toggle sync failed', err))
  }, [])

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
