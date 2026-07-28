import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Sparkles, BookMarked, MessageCircle } from 'lucide-react'
import { toast } from 'sonner'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { PlayerControls } from '../components/player/PlayerControls'
import { EpisodeReferences } from '../components/player/EpisodeReferences'
import { LyricsView } from '../components/lyrics/LyricsView'
import { PlayerBottomBar } from '../components/player/PlayerBottomBar'
import { WordCardPanel } from '../components/wordcard/WordCardPanel'
import { VocabDrawer } from '../components/vocab/VocabDrawer'
import type { Episode, Cue } from '../types/episode'
import type { DictEntry } from '../api/types'
import { api } from '../api'
import { usePlayer, useDailyOrder, useSettings, useActivity, useVocab } from '../state'
import { findActiveCueIndex, buildConversationPrompt, filterDueDeck } from '../lib'

export function PlayerRoute() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [selectedWord, setSelectedWord] = useState<string | null>(null)
  const [selectedCue, setSelectedCue] = useState<Cue | null>(null)
  const [dictEntry, setDictEntry] = useState<DictEntry | null>(null)
  const [isWordCardOpen, setIsWordCardOpen] = useState(false)
  const [loopCueIdx, setLoopCueIdx] = useState<number | null>(null)
  const [isVocabDrawerOpen, setIsVocabDrawerOpen] = useState(false)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const { currentTime, isPlaying, duration, seekTo, play, pause, loadProgress, setPlaybackRate, loadState, currentEpisode, setCurrentEpisode, getSegmentPlayer } = usePlayer()
  const { settings } = useSettings()
  const { markPlayed } = useDailyOrder()
  const { addListenMinutes, addLookupCount, markListened } = useActivity()
  const { items: vocabItems } = useVocab()
  const episodeIdRef = useRef<string | null>(null)
  const hasMarkedListened = useRef(false)
  const hasMarkedDailyPlayed = useRef(false)
  const initialSeekAppliedRef = useRef(false)
  const hasNotifiedDueRef = useRef(false)
  const resumePlaybackAfterWordCardRef = useRef(false)

  const loadEpisode = useCallback(async () => {
    setFetchError(null)
    setLoopCueIdx(null)
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
      hasNotifiedDueRef.current = false
    }
  }, [episode])

  useEffect(() => {
    // 推到全域 PlayerProvider：離開播放頁後 GlobalAudioHost/MiniPlayer 才知道現在播誰。
    if (episode) setCurrentEpisode(episode)
  }, [episode, setCurrentEpisode])

  useEffect(() => {
    if (!episode || initialSeekAppliedRef.current) return
    const episodeId = episode.id
    // 全域 PlayerProvider 已在播這集（例：從 MiniPlayer 點回播放頁）→ currentTime
    // 才是事實。localStorage 進度是節流快照，會落後幾百毫秒到 1 秒，
    // 拿它去 seek 就是使用者看到的「點進來倒退一下」。只有冷啟動才需要續播定位。
    if (currentEpisode?.id === episodeId && currentTime > 0) {
      initialSeekAppliedRef.current = true
      loadProgress(episodeId) // 副作用：綁定 provider 的 currentEpisodeIdRef，續存進度
      return
    }
    const progress = loadProgress(episodeId)
    if (!progress.exists) return

    // 等 hook loadState === 'ready'（segments decode 完成）才能 seek，否則會定位到 0。
    if (loadState !== 'ready') return
    initialSeekAppliedRef.current = true
    seekTo(progress.currentTime)
  }, [episode, currentEpisode, currentTime, loadProgress, loadState, seekTo])

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

  useEffect(() => {
    if (!episode || loopCueIdx === null) return
    const cue = episode.cues[loopCueIdx]
    if (!cue || (currentTime >= cue.start && currentTime < cue.end)) return
    seekTo(cue.start)
    play()
  }, [currentTime, episode, loopCueIdx, play, seekTo])

  const lookupWord = async (word: string) => {
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

  // iOS Safari gesture unlock：必須在 click handler 同步路徑內 ctx.resume() 才有效。
  // 包成 helper 讓所有「play」入口（cue click / next / replay）都走同一條路徑。
  const playWithUnlock = useCallback(() => {
    void getSegmentPlayer().unlock()
    void play()
  }, [play, getSegmentPlayer])

  const handleWordClick = async (word: string, cue: Cue) => {
    if (!settings.popupEnabled) return
    resumePlaybackAfterWordCardRef.current = isPlaying
    if (isPlaying) pause()
    setSelectedWord(word)
    setSelectedCue(cue)
    setIsWordCardOpen(true)
    await lookupWord(word)
  }

  const closeWordCard = useCallback(() => {
    const shouldResume = resumePlaybackAfterWordCardRef.current
    resumePlaybackAfterWordCardRef.current = false
    setIsWordCardOpen(false)
    if (shouldResume) playWithUnlock()
  }, [playWithUnlock])

  const handleReplayCue = () => {
    if (!episode || !selectedCue) return
    const cueIdx = episode.cues.indexOf(selectedCue)
    if (loopCueIdx !== null && cueIdx >= 0) setLoopCueIdx(cueIdx)
    seekTo(selectedCue.start)
    resumePlaybackAfterWordCardRef.current = true
    closeWordCard()
  }

  const handleCueClick = useCallback((cue: Cue) => {
    if (loopCueIdx !== null && episode) {
      const cueIdx = episode.cues.indexOf(cue)
      if (cueIdx >= 0) setLoopCueIdx(cueIdx)
    }
    seekTo(cue.start)
    playWithUnlock()
  }, [episode, loopCueIdx, seekTo, playWithUnlock])

  const handleCueLoopToggle = useCallback(() => {
    if (loopCueIdx !== null) {
      setLoopCueIdx(null)
      return
    }
    if (!episode || activeCueIdx < 0) return
    const cue = episode.cues[activeCueIdx]
    if (!cue) return
    setLoopCueIdx(activeCueIdx)
    seekTo(cue.start)
    playWithUnlock()
  }, [activeCueIdx, episode, loopCueIdx, playWithUnlock, seekTo])

  const handleNextCue = useCallback(() => {
    if (!episode) return
    const nextCueIdx = activeCueIdx + 1
    const nextCue = episode.cues[nextCueIdx]
    if (!nextCue) return
    if (loopCueIdx !== null) setLoopCueIdx(nextCueIdx)
    seekTo(nextCue.start)
  }, [activeCueIdx, episode, loopCueIdx, seekTo])

  const handleLookupRetry = () => {
    if (selectedWord) void lookupWord(selectedWord)
  }

  useEffect(() => {
    // 播完（<audio> 是全域節點，改用 currentTime/duration 逼近判斷取代 onEnded 事件）
    if (!episode || duration <= 0 || hasNotifiedDueRef.current) return
    if (currentTime < duration - 0.25) return
    const dueCount = filterDueDeck(vocabItems).length
    if (dueCount === 0) return
    hasNotifiedDueRef.current = true
    toast(`還有 ${dueCount} 個單字到期待複習`, {
      action: { label: '去複習', onClick: () => navigate('/flashcards') },
    })
  }, [currentTime, duration, episode, vocabItems, navigate])

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
  const isCueLooping = loopCueIdx !== null
  const canLoopCue = isCueLooping || activeCueIdx >= 0
  const cueDuration = episode.cues[episode.cues.length - 1]?.end ?? 0
  const playerDuration = duration > 0 ? duration : cueDuration

  return (
    <div className="bg-bg-canvas h-[calc(100dvh-56px-env(safe-area-inset-top,0px))] overflow-hidden text-text-primary flex flex-col">
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
        {/* 行動版參考資料浮動卡：LyricsView 自帶滾動（無外部 container 可掛），
            故以 absolute 浮在 main 的 padding-bottom 區；桌面版同一元件進 footer。 */}
        {episode.references && episode.references.length > 0 && (
          <div className="lg:hidden absolute inset-x-4 bottom-4 z-20">
            <EpisodeReferences references={episode.references} />
          </div>
        )}
      </main>

      {/* 控制列（桌面） */}
      <footer className="hidden lg:block fixed bottom-0 left-0 right-0 z-30 px-8 pb-6 pt-4 bg-bg-primary border-t border-border">
        <PlayerControls
          duration={playerDuration}
          isCueLooping={isCueLooping}
          canLoopCue={canLoopCue}
          onCueLoopToggle={handleCueLoopToggle}
        />
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
        {/* 桌面版參考資料：放在 footer 底（無來源時 component 直接回 null 不佔空間） */}
        {episode.references && episode.references.length > 0 && (
          <div className="mt-3 max-w-md mx-auto">
            <EpisodeReferences references={episode.references} />
          </div>
        )}
      </footer>

      {/* mobile bottom bar */}
      <PlayerBottomBar
        duration={playerDuration}
        cues={episode.cues}
        activeCueIdx={activeCueIdx}
        isCueLooping={isCueLooping}
        canLoopCue={canLoopCue}
        onCueLoopToggle={handleCueLoopToggle}
        onNextCue={handleNextCue}
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
        onClose={closeWordCard}
        onReplayCue={handleReplayCue}
      />

      {/* 單字本側拉面板 */}
      <VocabDrawer
        isOpen={isVocabDrawerOpen}
        onClose={() => setIsVocabDrawerOpen(false)}
      />
    </div>
  )
}
