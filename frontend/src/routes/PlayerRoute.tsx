import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { Sparkles, BookMarked, MessageCircle } from 'lucide-react'
import { toast } from 'sonner'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { AudioPlayer } from '../components/player/AudioPlayer'
import { PlayerControls } from '../components/player/PlayerControls'
import { LyricsView } from '../components/lyrics/LyricsView'
import { PlayerBottomBar } from '../components/player/PlayerBottomBar'
import { WordCardPanel } from '../components/wordcard/WordCardPanel'
import { VocabDrawer } from '../components/vocab/VocabDrawer'
import type { Episode, Cue } from '../types/episode'
import type { DictEntry } from '../api/types'
import { api } from '../api'
import { usePlayer, useDailyOrder, useSettings, useActivity, useVocab } from '../state'
import { findActiveCueIndex, buildConversationPrompt } from '../lib'

export function PlayerRoute() {
  const { id } = useParams<{ id: string }>()
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [selectedWord, setSelectedWord] = useState<string | null>(null)
  const [selectedCue, setSelectedCue] = useState<Cue | null>(null)
  const [dictEntry, setDictEntry] = useState<DictEntry | null>(null)
  const [isWordCardOpen, setIsWordCardOpen] = useState(false)
  const [isVocabDrawerOpen, setIsVocabDrawerOpen] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const { currentTime, duration, seekTo, play, videoRef, loadProgress, setPlaybackRate } = usePlayer()
  const { settings } = useSettings()
  const { markPlayed } = useDailyOrder()
  const { addListenMinutes, addLookupCount, markListened } = useActivity()
  const { items: vocabItems } = useVocab()
  const episodeIdRef = useRef<string | null>(null)
  const hasMarkedListened = useRef(false)
  const hasMarkedDailyPlayed = useRef(false)
  const initialSeekAppliedRef = useRef(false)

  const loadEpisode = useCallback(async () => {
    setFetchError(null)
    try {
      // ?date= 連結：DailyRoute 帶日期過來，先查當天交付；找不到（尚未生成／不歸屬）
      // fallback 到 listEpisodes()[0]，避免擋使用者。
      const dateParam = new URLSearchParams(window.location.search).get('date')
      if (dateParam) {
        const delivered = await api.getDeliveredEpisode(dateParam)
        if (delivered) {
          setEpisode(delivered)
          return
        }
      }
      if (id) {
        const data = await api.getEpisode(id)
        setEpisode(data)
        return
      }
      const list = await api.listEpisodes()
      if (list.length === 0) {
        setFetchError('目前沒有可播放的集數')
        return
      }
      const data = await api.getEpisode(list[0].id)
      setEpisode(data)
    } catch {
      setFetchError('節目資料載入失敗，請重新整理頁面')
    }
  }, [id])

  useEffect(() => {
    // 非同步資料載入的標準模式：setState 都在 await 之後才發生，
    // 不會造成 render 迴圈；規則誤報，抑制之。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadEpisode()
  }, [loadEpisode])

  useEffect(() => {
    if (episode && episode.id !== episodeIdRef.current) {
      episodeIdRef.current = episode.id
      initialSeekAppliedRef.current = false
      hasMarkedListened.current = false
    }
  }, [episode])

  useEffect(() => {
    if (!episode || initialSeekAppliedRef.current) return
    const progress = loadProgress(episode.id)
    if (!progress.exists) return
    const trySeek = () => {
      if (initialSeekAppliedRef.current) return
      const v = videoRef.current
      if (v && v.readyState >= 1) {
        initialSeekAppliedRef.current = true
        seekTo(progress.currentTime)
      } else {
        setTimeout(trySeek, 100)
      }
    }
    trySeek()
  }, [episode, loadProgress, seekTo, videoRef])

  useEffect(() => {
    if (!episode || duration <= 0 || hasMarkedListened.current) return
    if (currentTime / duration > 0.8) {
      hasMarkedListened.current = true
      markListened(episode.id)
      const ymMin = new Date().toLocaleDateString('en-CA').slice(0, 7)
      addListenMinutes(ymMin, Math.floor(currentTime / 60))
    }
  }, [currentTime, duration, episode, markListened, addListenMinutes])

  useEffect(() => {
    if (!episode || duration <= 0 || hasMarkedDailyPlayed.current) return
    if (currentTime / duration >= 0.9) {
      hasMarkedDailyPlayed.current = true
      void markPlayed(new Date().toLocaleDateString('en-CA'))
    }
  }, [currentTime, duration, episode, markPlayed])

  useEffect(() => {
    setPlaybackRate(settings.playbackRate)
  }, [settings.playbackRate, setPlaybackRate])

  const activeCueIdx = useMemo(
    () => episode ? findActiveCueIndex(episode.cues, currentTime) : -1,
    [episode, currentTime],
  )

  const handleWordClick = async (word: string, cue: Cue) => {
    if (!settings.popupEnabled) return
    setSelectedWord(word)
    setSelectedCue(cue)
    setIsWordCardOpen(true)
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
  }

  const handleCueClick = useCallback((cue: Cue) => {
    seekTo(cue.start)
    play()
  }, [seekTo, play])

  const handleLookupRetry = () => {
    if (selectedWord && selectedCue) {
      void handleWordClick(selectedWord, selectedCue)
    }
  }

  const handleCopyPrompt = async () => {
    if (!episode) return
    const prompt = buildConversationPrompt({
      episodeTitle: episode.title,
      cues: episode.cues,
      cefrLevel: settings.cefrLevel,
      vocab: vocabItems.filter(v => v.sourceEpisodeId === episode.id),
    })
    try {
      await navigator.clipboard.writeText(prompt)
      toast.success('已複製！貼到 Gemini 或 ChatGPT 語音對話就能開始練習')
    } catch (err) {
      console.error(err)
      toast.error('複製失敗，請重試')
    }
  }

  if (fetchError !== null) {
    return (
      <ErrorBanner message={fetchError} onRetry={() => void loadEpisode()} retryLabel="重新載入" className="h-64" />
    )
  }

  if (!episode) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <Sparkles size={24} className="text-accent animate-pulse" />
        <p className="text-text-tertiary text-sm">載入中…</p>
      </div>
    )
  }

  const selectedCueIdx = selectedCue ? episode.cues.indexOf(selectedCue) : -1

  return (
    <div className="bg-bg-canvas h-[calc(100dvh-56px-env(safe-area-inset-top,0px))] overflow-hidden text-text-primary flex flex-col">
      {/* 隱形音檔綁時間軸（不上視） */}
      <AudioPlayer audioUrl={episode.audioUrl} />

      {/* 大歌詞：佔滿中間剩餘空間，封面與標題作為第一個 scroll item 一起滾動 */}
      <main className="flex-1 min-h-0 relative pb-[100px] lg:pb-40">
        <LyricsView
          episodeId={episode.id}
          episodeTitle={episode.title}
          cues={episode.cues}
          currentTime={currentTime}
          onWordClick={handleWordClick}
          onCueClick={handleCueClick}
        />
      </main>

      {/* 控制列（桌面） */}
      <footer className="hidden lg:block fixed bottom-0 left-0 right-0 z-30 px-8 pb-6 pt-4 bg-bg-primary border-t border-border">
        <PlayerControls duration={episode.cues[episode.cues.length - 1]?.end ?? 0} />
        <div className="flex items-center justify-center gap-4 mt-3">
          <button
            onClick={() => void handleCopyPrompt()}
            className="flex items-center gap-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors"
          >
            <MessageCircle size={14} />
            複製對話練習 Prompt
          </button>
          <button
            onClick={() => setIsVocabDrawerOpen(true)}
            className="flex items-center gap-1.5 text-xs text-text-secondary hover:text-text-primary transition-colors"
          >
            <BookMarked size={14} />
            我的單字本
          </button>
        </div>
      </footer>

      {/* mobile bottom bar */}
      <PlayerBottomBar
        duration={episode.cues[episode.cues.length - 1]?.end ?? 0}
        cues={episode.cues}
        activeCueIdx={activeCueIdx}
        onCopyPrompt={() => void handleCopyPrompt()}
        onVocabOpen={() => setIsVocabDrawerOpen(true)}
      />

      {/* 詞卡面板 */}
      <WordCardPanel
        isOpen={isWordCardOpen}
        word={selectedWord}
        entry={dictEntry}
        lookupError={lookupError}
        onRetry={handleLookupRetry}
        activeCue={selectedCue}
        episodeId={episode.id}
        activeCueIdx={selectedCueIdx}
        onClose={() => setIsWordCardOpen(false)}
        onReplayCue={() => {
          if (!selectedCue) return
          seekTo(selectedCue.start)
          play()
          setIsWordCardOpen(false)
        }}
      />

      {/* 單字本側拉面板 */}
      <VocabDrawer
        isOpen={isVocabDrawerOpen}
        onClose={() => setIsVocabDrawerOpen(false)}
      />
    </div>
  )
}
