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
  /** 通過學習模式 → status 2、明天首複 */
  completeLearning(id: string): Promise<void>
  /** 畢業測驗一輪結果（見 lib/quiz.applyQuizRound） */
  applyQuizRound(id: string, passed: boolean): Promise<void>
  /** 精熟字重新加入複習 */
  reviveVocab(id: string): Promise<void>
}

export const VocabContext = createContext<VocabContextValue | null>(null)
