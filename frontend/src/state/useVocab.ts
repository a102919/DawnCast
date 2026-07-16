import { useContext } from 'react'
import { VocabContext, type VocabContextValue } from './vocabContextValue'

export function useVocab(): VocabContextValue {
  const ctx = useContext(VocabContext)
  if (!ctx) throw new Error('useVocab must be used inside VocabProvider')
  return ctx
}
