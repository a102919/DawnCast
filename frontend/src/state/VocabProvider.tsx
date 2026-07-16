import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, type VocabItem } from '../api'
import { VocabContext, type VocabContextValue } from './vocabContextValue'

function sm2(quality: number, prevInterval: number, prevEase: number): { interval: number; ease: number } {
  const ease = Math.max(1.3, prevEase + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
  if (quality < 3) return { interval: 1, ease }
  if (prevInterval <= 1) return { interval: 6, ease }
  return { interval: Math.round(prevInterval * prevEase), ease }
}

function todayPlusDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toLocaleDateString('en-CA')
}

export function VocabProvider({ children }: { readonly children: ReactNode }) {
  const [items, setItems] = useState<VocabItem[]>([])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    api.listVocab()
      .then(list => {
        if (cancelled) return
        setItems(list)
        setIsLoading(false)
      })
      .catch(err => {
        // ponytail: 不吞錯也不閃退——把錯誤丟到 console 讓 dev 能看到，
        // 但 isLoading 還是要 set false 才不會永遠轉圈、UI 顯示空狀態。
        console.error('listVocab failed:', err)
        if (!cancelled) setIsLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const addVocab = useCallback(async (item: Omit<VocabItem, 'id' | 'createdAt'>) => {
    const newItem = await api.addVocab(item)
    setItems(prev => {
      if (prev.some(v => v.id === newItem.id)) return prev
      return [newItem, ...prev]
    })
  }, [])

  const removeVocab = useCallback(async (id: string) => {
    await api.removeVocab(id)
    setItems(prev => prev.filter(v => v.id !== id))
  }, [])

  const clearVocab = useCallback(async () => {
    await api.clearVocab()
    setItems([])
  }, [])

  const isInVocab = useCallback((lemma: string) => {
    return items.some(v => v.lemma === lemma)
  }, [items])

  const updateCardReview = useCallback(async (id: string, quality: number) => {
    const item = items.find(i => i.id === id)
    if (!item) return
    const { interval, ease } = sm2(quality, item.interval ?? 1, item.ease ?? 2.5)
    const nextReview = todayPlusDays(interval)
    await api.updateVocab(id, { nextReview, interval, ease })
    setItems(prev => prev.map(v => v.id === id ? { ...v, nextReview, interval, ease } : v))
  }, [items])

  const value: VocabContextValue = {
    items, isLoading, addVocab, removeVocab, clearVocab, isInVocab, updateCardReview,
  }

  return (
    <VocabContext.Provider value={value}>
      {children}
    </VocabContext.Provider>
  )
}
