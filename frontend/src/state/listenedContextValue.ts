import { createContext } from 'react'

export type ListenedContextValue = {
  readonly listenedIds: ReadonlySet<string>
  markAsListened(id: string): void
}

export const ListenedContext = createContext<ListenedContextValue | null>(null)
