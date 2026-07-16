import { createContext } from 'react'

export type FavoritesContextValue = {
  readonly favorites: ReadonlySet<string>
  toggle(id: string): Promise<void>
  has(id: string): boolean
}

export const FavoritesContext = createContext<FavoritesContextValue | null>(null)
