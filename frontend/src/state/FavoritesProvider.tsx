import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api } from '../api'
import { FavoritesContext, type FavoritesContextValue } from './favoritesContextValue'

export function FavoritesProvider({ children }: { readonly children: ReactNode }) {
  const [favorites, setFavorites] = useState<ReadonlySet<string>>(new Set())

  useEffect(() => {
    api.getFavorites().then(ids => setFavorites(new Set(ids)))
  }, [])

  const toggle = useCallback(async (id: string) => {
    setFavorites(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
        void api.removeFavorite(id)
      } else {
        next.add(id)
        void api.addFavorite(id)
      }
      return next
    })
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
