import { createContextHook } from './createContextHook'
import { FavoritesContext, type FavoritesContextValue } from './favoritesContextValue'

export const useFavorites: () => FavoritesContextValue = createContextHook(
  FavoritesContext,
  'useFavorites',
  'FavoritesProvider',
)
