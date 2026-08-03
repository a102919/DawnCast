import { createContext } from 'react'
import type { VocabItem } from '../api'

export type ReviewMode = 'review' | 'practice'

export type VocabContextValue = {
  readonly items: readonly VocabItem[]
  readonly isLoading: boolean
  /** 載入失敗訊息；非空時 UI 顯示錯誤空狀態而非「單字本是空的」 */
  readonly error: string | null
  addVocab(item: Omit<VocabItem, 'id' | 'createdAt'>): Promise<void>
  removeVocab(id: string): Promise<void>
  clearVocab(): Promise<void>
  isInVocab(lemma: string): boolean
  /** review=走 sm2 全寫入排程；practice=答錯把 nextReview 提前 1 天，答對不寫 */
  updateCardReview(id: string, quality: number, opts?: { readonly mode?: ReviewMode }): Promise<void>
  /** 通過學習模式 → status 2、明天首複 */
  completeLearning(id: string): Promise<void>
  /** 畢業測驗一輪結果（見 lib/quiz.applyQuizRound） */
  applyQuizRound(id: string, passed: boolean): Promise<void>
  /** 精熟字重新加入複習 */
  reviveVocab(id: string): Promise<void>
  /** 重新抓單字列表（error 後重試用） */
  reload(): Promise<void>
}

export const VocabContext = createContext<VocabContextValue | null>(null)
