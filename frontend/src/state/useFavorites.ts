import { useContext } from 'react'
import { FavoritesContext, type FavoritesContextValue } from './favoritesContextValue'

export function useFavorites(): FavoritesContextValue {
  const ctx = useContext(FavoritesContext)
  if (!ctx) throw new Error('useFavorites must be used inside FavoritesProvider')
  return ctx
}
