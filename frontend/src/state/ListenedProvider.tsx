import { useCallback, useState, type ReactNode } from 'react'
import { ListenedContext, type ListenedContextValue } from './listenedContextValue'

const LS_KEY = 'dawncast:listened'

export function ListenedProvider({ children }: { readonly children: ReactNode }) {
  const [listenedIds, setListenedIds] = useState<ReadonlySet<string>>(() => {
    try {
      const stored = localStorage.getItem(LS_KEY)
      return new Set(stored ? (JSON.parse(stored) as string[]) : [])
    } catch {
      return new Set()
    }
  })

  const markAsListened = useCallback((id: string) => {
    setListenedIds(prev => {
      if (prev.has(id)) return prev
      const next = new Set(prev)
      next.add(id)
      localStorage.setItem(LS_KEY, JSON.stringify([...next]))
      const today = new Date().toLocaleDateString('en-CA')
      const datesKey = 'dawncast:activity:dates'
      const rawDates = localStorage.getItem(datesKey)
      const existingDates: string[] = rawDates ? (JSON.parse(rawDates) as string[]) : []
      if (!existingDates.includes(today)) {
        localStorage.setItem(datesKey, JSON.stringify([...existingDates, today].slice(-365)))
      }
      return next
    })
  }, [])

  const value: ListenedContextValue = { listenedIds, markAsListened }

  return (
    <ListenedContext.Provider value={value}>
      {children}
    </ListenedContext.Provider>
  )
}
