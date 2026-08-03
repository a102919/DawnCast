import { createContextHook } from './createContextHook'
import { VocabContext, type VocabContextValue } from './vocabContextValue'

export const useVocab: () => VocabContextValue = createContextHook(
  VocabContext,
  'useVocab',
  'VocabProvider',
)
