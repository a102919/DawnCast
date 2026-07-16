import { createContext } from 'react'
import type { VocabItem } from '../api'

export type VocabContextValue = {
  readonly items: VocabItem[]
  readonly isLoading: boolean
  addVocab(item: Omit<VocabItem, 'id' | 'createdAt'>): Promise<void>
  removeVocab(id: string): Promise<void>
  clearVocab(): Promise<void>
  isInVocab(lemma: string): boolean
  updateCardReview(id: string, quality: number): Promise<void>
}

export const VocabContext = createContext<VocabContextValue | null>(null)
