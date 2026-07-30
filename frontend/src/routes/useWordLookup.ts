import { useCallback, useRef, useState } from 'react'
import { api } from '../api'
import type { DictEntry } from '../api/types'
import type { Cue } from '../types/episode'

export interface UseWordLookupResult {
  readonly selectedWord: string | null
  readonly selectedCue: Cue | null
  readonly dictEntry: DictEntry | null
  readonly isWordCardOpen: boolean
  readonly lookupError: string | null
  /** 點字幕單字：暫停播放（若正在播）、開詞卡、查字典。 */
  open(word: string, cue: Cue): Promise<void>
  /** 關閉詞卡；開卡前若正在播放則自動恢復播放。 */
  close(): void
  /** 查詢失敗時重試同一個字。 */
  retry(): void
}

export interface UseWordLookupParams {
  readonly isPlaying: boolean
  pause(): void
  playWithUnlock(): void
  addLookupCount(month: string, count: number): void
}

/** 字典查詢 + retry：點字幕單字彈出詞卡、查字典、關卡時視情況恢復播放。 */
export function useWordLookup({ isPlaying, pause, playWithUnlock, addLookupCount }: UseWordLookupParams): UseWordLookupResult {
  const [selectedWord, setSelectedWord] = useState<string | null>(null)
  const [selectedCue, setSelectedCue] = useState<Cue | null>(null)
  const [dictEntry, setDictEntry] = useState<DictEntry | null>(null)
  const [isWordCardOpen, setIsWordCardOpen] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const resumePlaybackRef = useRef(false)

  const lookupWord = useCallback(async (word: string) => {
    setDictEntry(null)
    setLookupError(null)
    try {
      const entry = await api.lookupDict(word)
      setDictEntry(entry)
      const ymLookup = new Date().toLocaleDateString('en-CA').slice(0, 7)
      addLookupCount(ymLookup, 1)
    } catch {
      setLookupError('查詢失敗，請重試')
    }
  }, [addLookupCount])

  const open = useCallback(async (word: string, cue: Cue) => {
    resumePlaybackRef.current = isPlaying
    if (isPlaying) pause()
    setSelectedWord(word)
    setSelectedCue(cue)
    setIsWordCardOpen(true)
    await lookupWord(word)
  }, [isPlaying, pause, lookupWord])

  const close = useCallback(() => {
    const shouldResume = resumePlaybackRef.current
    resumePlaybackRef.current = false
    setIsWordCardOpen(false)
    if (shouldResume) playWithUnlock()
  }, [playWithUnlock])

  const retry = useCallback(() => {
    if (selectedWord) void lookupWord(selectedWord)
  }, [selectedWord, lookupWord])

  return { selectedWord, selectedCue, dictEntry, isWordCardOpen, lookupError, open, close, retry }
}
