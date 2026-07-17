import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertCircle, RotateCcw, PlayCircle } from 'lucide-react'
import { VideoPlayer } from '../components/player/VideoPlayer'
import { PlayerControls } from '../components/player/PlayerControls'
import { CueDisplay } from '../components/player/CueDisplay'
import { PlayerBottomBar } from '../components/player/PlayerBottomBar'
import { TranscriptPanel } from '../components/transcript/TranscriptPanel'
import { MobileTranscriptSheet } from '../components/transcript/MobileTranscriptSheet'
import { WordCardPanel } from '../components/wordcard/WordCardPanel'
import { VocabDrawer } from '../components/vocab/VocabDrawer'
import type { Episode, Cue } from '../types/episode'
import type { DictEntry } from '../api/types'
import { api } from '../api'
import { usePlayer, useListened, useDailyOrder, useSettings, useActivity } from '../state'
import { findActiveCueIndex } from '../lib'

export function PlayerRoute() {
  const { id } = useParams<{ id: string }>()
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [selectedWord, setSelectedWord] = useState<string | null>(null)
  const [selectedCue, setSelectedCue] = useState<Cue | null>(null)
  const [dictEntry, setDictEntry] = useState<DictEntry | null>(null)
  const [isWordCardOpen, setIsWordCardOpen] = useState(false)
  const [isTranscriptOpen, setIsTranscriptOpen] = useState(false)
  const [isVocabDrawerOpen, setIsVocabDrawerOpen] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const { currentTime, duration, seekTo, play, videoRef, loadProgress, setPlaybackRate } = usePlayer()
  const { settings } = useSettings()
  const { markAsListened } = useListened()
  const { markPlayed } = useDailyOrder()
  const { addListenMinutes, addLookupCount } = useActivity()
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
    // mount 時載入 episode：async data loading 是 effect 內 setState 的正當用法
    // (https://react.dev/reference/react/useEffect#fetching-data-with-effects)
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
    // P0-1：載入已儲存播放進度。loadProgress 本身會先看本機 localStorage，
    // 沒有的話退回 ActivityProvider 的 lastPlayed（GET /activity 非同步回來後
    // loadProgress 參照會變、這個 effect 因此重跑，達成換裝置後補套用進度）。
    if (!episode || initialSeekAppliedRef.current) return
    const progress = loadProgress(episode.id)
    if (!progress.exists) return
    // 等 video element 就緒後套用
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
      markAsListened(episode.id)
      const ymMin = new Date().toLocaleDateString('en-CA').slice(0, 7)
      addListenMinutes(ymMin, Math.floor(currentTime / 60))
    }
  }, [currentTime, duration, episode, markAsListened, addListenMinutes])

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

  const allSpeakers = useMemo(
    () => episode ? [...new Set(episode.cues.map(c => c.speaker))] : [],
    [episode],
  )

  const handleWordClick = async (word: string, cue: Cue) => {
    setSelectedWord(word)
    setSelectedCue(cue)
    setIsWordCardOpen(true)
    setDictEntry(null)
    setLookupError(null)
    try {
      const entry = await api.lookupDict(word)
      setDictEntry(entry)
      // 寫入活動查詞計數（給 ProgressRoute 顯示用）
      const ymLookup = new Date().toLocaleDateString('en-CA').slice(0, 7)
      addLookupCount(ymLookup, 1)
    } catch {
      setLookupError('查詢失敗，請重試')
    }
  }

  const handleLookupRetry = () => {
    if (selectedWord && selectedCue) {
      void handleWordClick(selectedWord, selectedCue)
    }
  }

  if (fetchError !== null) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <AlertCircle size={32} className="text-danger" />
        <p className="text-danger text-sm">{fetchError}</p>
        <button
          className="flex items-center gap-1.5 px-4 py-2 text-sm text-text-secondary bg-bg-secondary hover:bg-border rounded-md transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          onClick={() => void loadEpisode()}
        >
          <RotateCcw size={14} />
          重新載入
        </button>
      </div>
    )
  }

  if (!episode) {
    return (
      <div className="flex flex-col lg:flex-row h-[calc(100dvh-152px)] lg:h-[calc(100dvh-56px)] overflow-hidden">
        {/* 左側骨架 */}
        <div className="flex flex-col flex-1 min-w-0 overflow-y-auto">
          <div className="p-4 lg:p-5 animate-pulse">
            {/* VideoPlayer 佔位（16:9） */}
            <div className="w-full aspect-video rounded-lg bg-bg-secondary" />
            {/* PlayerControls 佔位（desktop only） */}
            <div className="mt-3 space-y-2 hidden lg:block">
              <div className="h-1.5 rounded-full bg-bg-secondary" />
              <div className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-full bg-bg-secondary" />
                <div className="h-8 w-8 rounded-full bg-bg-secondary" />
                <div className="h-8 w-8 rounded-full bg-bg-secondary" />
                <div className="ml-auto h-4 w-20 rounded bg-bg-secondary" />
              </div>
            </div>
            {/* CueDisplay 佔位 */}
            <div className="mt-4 space-y-2">
              <div className="h-5 w-3/4 rounded bg-bg-secondary" />
              <div className="h-5 w-1/2 rounded bg-bg-secondary" />
            </div>
          </div>
        </div>
        {/* 右側逐字稿骨架（desktop） */}
        <div className="hidden lg:flex flex-col w-96 border-l border-border gap-3 p-4 animate-pulse">
          <div className="h-5 w-1/3 rounded bg-bg-secondary" />
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="space-y-1.5">
              <div className="h-3 w-1/4 rounded bg-bg-secondary" />
              <div className="h-4 rounded bg-bg-secondary" />
              <div className="h-4 w-5/6 rounded bg-bg-secondary" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  const activeCue = activeCueIdx >= 0 ? episode.cues[activeCueIdx] : null
  const selectedCueIdx = selectedCue ? episode.cues.indexOf(selectedCue) : -1

  return (
    <div className="flex flex-col lg:flex-row h-[calc(100dvh-152px)] lg:h-[calc(100dvh-56px)] overflow-hidden">
      {/* 左側：影片 + 字幕 */}
      <div className="flex flex-col flex-1 min-w-0 overflow-y-auto">
        <div className="p-4 lg:p-5">
          <VideoPlayer videoUrl={episode.videoUrl} />
          <div className="mt-3 hidden lg:block">
            <PlayerControls duration={episode.cues[episode.cues.length - 1]?.end ?? 0} />
          </div>
          <div className="mt-4">
            <AnimatePresence mode="wait">
              {activeCue ? (
                <motion.div
                  key={activeCue.index}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
                >
                  <CueDisplay cue={activeCue} onWordClick={handleWordClick} allSpeakers={allSpeakers} />
                </motion.div>
              ) : (
                <motion.div
                  key="placeholder"
                  className="flex items-center gap-2 text-text-tertiary"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
                >
                  <PlayCircle size={16} className="shrink-0" />
                  <span className="text-sm">按播放開始學習</span>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>

      {/* 右側：Transcript Panel（desktop 固定，mobile 可折疊） */}
      <div className="hidden lg:block w-96 border-l border-border overflow-hidden">
        <TranscriptPanel
          cues={episode.cues}
          activeCueIdx={activeCueIdx}
          onWordClick={handleWordClick}
          onCueClick={() => {
            // seekTo handled by TranscriptPanel directly
          }}
        />
      </div>

      {/* Mobile 逐字稿底部 Sheet */}
      <MobileTranscriptSheet
        isOpen={isTranscriptOpen}
        cues={episode.cues}
        activeCueIdx={activeCueIdx}
        onWordClick={handleWordClick}
        onClose={() => setIsTranscriptOpen(false)}
      />

      {/* 底部詞卡面板 */}
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

      {/* Mobile 統一播放控制面板（取代 BottomNav） */}
      <PlayerBottomBar
        duration={episode.cues[episode.cues.length - 1]?.end ?? 0}
        cues={episode.cues}
        activeCueIdx={activeCueIdx}
        onTranscriptOpen={() => setIsTranscriptOpen(true)}
        onVocabOpen={() => setIsVocabDrawerOpen(true)}
      />
    </div>
  )
}
