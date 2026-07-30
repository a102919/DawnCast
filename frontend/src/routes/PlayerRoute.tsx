import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { Sparkles, BookMarked, MessageCircle } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../api'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { PlayerControls } from '../components/player/PlayerControls'
import { EpisodeReferences } from '../components/player/EpisodeReferences'
import { LyricsView } from '../components/lyrics/LyricsView'
import { PlayerBottomBar } from '../components/player/PlayerBottomBar'
import { WordCardPanel } from '../components/wordcard/WordCardPanel'
import { VocabDrawer } from '../components/vocab/VocabDrawer'
import type { Cue } from '../types/episode'
import { usePlayer, useDailyOrder, useSettings, useActivity, useVocab } from '../state'
import { findActiveCueIndex, buildConversationPrompt, countActionable } from '../lib'
import { useEpisode } from './useEpisode'
import { useEpisodeProgress } from './useEpisodeProgress'
import { useCueLoop } from './useCueLoop'
import { useWordLookup } from './useWordLookup'

/** 單字本「跳到」／「前往該集」導頁帶的 router state：目標時間戳 + 收錄當下的
 *  精確 cue 索引（優先於時間戳，理由同 WordCardPanel/ReplayAudioButton 的浮點捨入註解）。 */
interface VocabSeekState {
  readonly seekTo: number
  readonly seekLineNo?: number
}

function parseVocabSeekState(state: unknown): VocabSeekState | null {
  if (typeof state !== 'object' || state === null) return null
  const seekTo = (state as Record<string, unknown>).seekTo
  return typeof seekTo === 'number' ? { seekTo, seekLineNo: (state as Record<string, unknown>).seekLineNo as number | undefined } : null
}

export function PlayerRoute() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const location = useLocation()
  // location.state 同一個 history entry 內參照穩定；parseVocabSeekState 每次呼叫都會
  // 建新物件，沒 memo 會讓依賴它的 effect 每次 render 都判斷成「變了」而重複 seekTo。
  const pendingSeek = useMemo(() => parseVocabSeekState(location.state), [location.state])
  const [isVocabDrawerOpen, setIsVocabDrawerOpen] = useState(false)
  const hasNotifiedDueRef = useRef(false)
  const dueNotifiedEpisodeIdRef = useRef<string | null>(null)

  const { currentTime, isPlaying, duration, seekTo, play, pause, loadProgress, setPlaybackRate, loadState, currentEpisode, setCurrentEpisode, getSegmentPlayer } = usePlayer()
  const { settings } = useSettings()
  const { markPlayed } = useDailyOrder()
  const { addListenMinutes, addLookupCount, markListened } = useActivity()
  const { items: vocabItems } = useVocab()

  const { episode, fetchError, orderId, reload } = useEpisode(id)

  useEffect(() => {
    // 推到全域 PlayerProvider：離開播放頁後 GlobalAudioHost/MiniPlayer 才知道現在播誰。
    if (episode) setCurrentEpisode(episode)
  }, [episode, setCurrentEpisode])

  useEpisodeProgress({
    episode, currentTime, duration, loadState, currentEpisode, orderId,
    skipResumeSeek: pendingSeek !== null,
    seekTo, loadProgress, markListened, addListenMinutes, markPlayed,
    recordPlay: api.recordEpisodePlay,
  })

  // 單字本「跳到」／「前往該集」的目標 seek：等這集 segments 真的 decode 完成
  // （loadState==='ready'）才動，不然會定位到 0；套用後清掉 state 避免重整/上一頁重播一次。
  useEffect(() => {
    if (!episode || !pendingSeek || loadState !== 'ready') return
    const idx = pendingSeek.seekLineNo !== undefined && pendingSeek.seekLineNo >= 0 && pendingSeek.seekLineNo < episode.cues.length
      ? pendingSeek.seekLineNo
      : Math.max(0, findActiveCueIndex(episode.cues, pendingSeek.seekTo))
    const cue = episode.cues[idx]
    seekTo(cue ? cue.start : pendingSeek.seekTo)
    navigate(location.pathname, { replace: true, state: null })
  }, [episode, loadState, pendingSeek, seekTo, navigate, location.pathname])

  useEffect(() => {
    setPlaybackRate(settings.playbackRate)
  }, [settings.playbackRate, setPlaybackRate])

  const activeCueIdx = useMemo(
    () => episode ? findActiveCueIndex(episode.cues, currentTime) : -1,
    [episode, currentTime],
  )

  // iOS Safari gesture unlock：必須在 click handler 同步路徑內 ctx.resume() 才有效。
  // 包成 helper 讓所有「play」入口（cue click / next / replay / cue loop toggle）都走同一條路徑。
  const playWithUnlock = useCallback(() => {
    void getSegmentPlayer().unlock()
    void play()
  }, [play, getSegmentPlayer])

  const cueLoop = useCueLoop({ episode, currentTime, activeCueIdx, isPlaying, seekTo, play, playWithUnlock })
  const { retarget: retargetCueLoop } = cueLoop
  const wordLookup = useWordLookup({ isPlaying, pause, playWithUnlock, addLookupCount })

  const handleWordClick = async (word: string, cue: Cue) => {
    if (!settings.popupEnabled) return
    await wordLookup.open(word, cue)
  }

  const handleCueClick = useCallback((cue: Cue) => {
    retargetCueLoop(cue)
    seekTo(cue.start)
    playWithUnlock()
  }, [retargetCueLoop, seekTo, playWithUnlock])

  useEffect(() => {
    if (episode && episode.id !== dueNotifiedEpisodeIdRef.current) {
      dueNotifiedEpisodeIdRef.current = episode.id
      hasNotifiedDueRef.current = false
    }
  }, [episode])

  useEffect(() => {
    // 播完（<audio> 是全域節點，改用 currentTime/duration 逼近判斷取代 onEnded 事件）
    if (!episode || duration <= 0 || hasNotifiedDueRef.current) return
    if (currentTime < duration - 0.25) return
    const dueCount = countActionable(vocabItems)
    if (dueCount === 0) return
    hasNotifiedDueRef.current = true
    toast(`還有 ${dueCount} 個單字待學習複習`, {
      action: { label: '去複習', onClick: () => navigate('/vocab') },
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
      <ErrorBanner message={fetchError} onRetry={() => void reload()} retryLabel="重新載入" className="h-64" />
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

  const selectedCueIdx = wordLookup.selectedCue ? episode.cues.indexOf(wordLookup.selectedCue) : -1
  const cueDuration = episode.cues[episode.cues.length - 1]?.end ?? 0
  const playerDuration = duration > 0 ? duration : cueDuration

  return (
    <div className="bg-bg-canvas h-[calc(100dvh-56px-env(safe-area-inset-top,0px))] overflow-hidden text-text-primary flex flex-col">
      {/* 大歌詞：佔滿中間剩餘空間，封面與標題作為第一個 scroll item 一起滾動 */}
      <main className="flex-1 min-h-0 overflow-hidden relative">
        <LyricsView
          episodeId={episode.id}
          episodeTitle={episode.title}
          cues={episode.cues}
          currentTime={currentTime}
          onWordClick={handleWordClick}
          onCueClick={handleCueClick}
          references={episode.references}
        />
      </main>

      {/* 控制列（桌面） */}
      <footer className="hidden lg:block fixed bottom-0 left-0 right-0 z-30 px-8 pb-6 pt-4 bg-bg-primary border-t border-border">
        <PlayerControls
          duration={playerDuration}
          isCueLooping={cueLoop.isCueLooping}
          canLoopCue={cueLoop.canLoopCue}
          onCueLoopToggle={cueLoop.toggle}
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
        isCueLooping={cueLoop.isCueLooping}
        canLoopCue={cueLoop.canLoopCue}
        onCueLoopToggle={cueLoop.toggle}
        onNextCue={cueLoop.next}
        onCopyPrompt={() => void handleCopyPrompt()}
        onVocabOpen={() => setIsVocabDrawerOpen(true)}
      />

      {/* 詞卡面板 */}
      <WordCardPanel
        isOpen={wordLookup.isWordCardOpen}
        word={wordLookup.selectedWord}
        entry={wordLookup.dictEntry}
        lookupError={wordLookup.lookupError}
        onRetry={wordLookup.retry}
        activeCue={wordLookup.selectedCue}
        episodeId={episode.id}
        activeCueIdx={selectedCueIdx}
        onClose={wordLookup.close}
      />

      {/* 單字本側拉面板 */}
      <VocabDrawer
        isOpen={isVocabDrawerOpen}
        onClose={() => setIsVocabDrawerOpen(false)}
      />
    </div>
  )
}
