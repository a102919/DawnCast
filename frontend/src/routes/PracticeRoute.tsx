/** 練習模式 route：/practice/:episodeId。沿用 useSegmentPlayer（共享 model 音檔引擎），
 *  額外掛第 4 顆 audio element 給 user recording 用（A/B 比較）。iOS 解鎖要求每顆
 *  audio element 各別在 user gesture stack 內解鎖；離開 route 時把 practiceEl remove()
 *  避免 unlock state leak。
 *
 *  設計遵循 apple-design 準則：
 *  - 觸控立即反饋（pointer-down 高亮、按鈕 :active scale 0.97）
 *  - 大字 transcript（Dynamic Type 相容）、System font、negative tracking on large
 *  - 控制列採 translucent material（backdrop-filter blur）
 *  - prefers-reduced-motion：去掉 spring，改 opacity cross-fade
 *  - 8 個設計原則：Purpose（只給練習需要的）、Agency（隨時可離開、單字可跳）、
 *    Familiarity（沿用 PlayerRoute 的 LyricsView 結構，不重新發明）
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ChevronLeft, Mic, Square, Repeat, Repeat1, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { LyricsView } from '../components/lyrics/LyricsView'
import type { Cue } from '../types/episode'
import { usePlayer } from '../state'
import { useEpisode } from './useEpisode'

/** 練習模式自有的 5 檔 speed（比 PlayerRoute 的 [0.75, 1, 1.25, 1.5] 多 0.5）。 */
const PRACTICE_RATES = [0.5, 0.75, 1, 1.25, 1.5] as const

function practiceStatusLabel(
  isRecording: boolean,
  isPlayingRecording: boolean,
  isPlaying: boolean,
  playbackRate: number,
): string {
  if (isRecording) return '錄音中…'
  if (isPlayingRecording) return '正在播放錄音'
  if (isPlaying) return `播放中（${playbackRate}x）`
  return '點任一單字開始練習'
}

/** 隱藏容器：給 practiceEl 一個 DOM 落腳處（iOS Safari 必要），跟 audioEngine.ts
 *  既有 mainA / mainB / previewEl 共享同一個 hidden host。 */
function appendToHost(el: HTMLAudioElement): void {
  const body = typeof document !== 'undefined' ? document.body : null
  if (!body) return
  let host = body.querySelector('[data-audio-host]')
  if (!host) {
    host = document.createElement('div')
    host.setAttribute('aria-hidden', 'true')
    host.setAttribute('data-audio-host', '')
    const hostEl = host as HTMLDivElement
    hostEl.style.cssText = 'position:absolute;width:0;height:0;opacity:0;pointer-events:none;overflow:hidden;'
    body.appendChild(hostEl)
  }
  host.appendChild(el)
}

export function PracticeRoute() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { episode, fetchError, reload } = useEpisode(id)
  const {
    seekTo, seekToWord, play, setPlaybackRate,
    playbackRate, currentTime, isPlaying,
    loadEpisode, setCurrentEpisode,
  } = usePlayer()

  // 跟 PlayerRoute 一樣把 episode 推到全域 PlayerProvider
  useEffect(() => {
    if (episode) setCurrentEpisode(episode)
  }, [episode, setCurrentEpisode])

  // 進入時 loadEpisode（player 還沒拿到 segments 就不能用）
  useEffect(() => {
    if (episode) loadEpisode(episode)
  }, [episode, loadEpisode])

  // 第 4 顆 audio element：user recording（model 仍走 mainA / mainB）
  const practiceElRef = useRef<HTMLAudioElement | null>(null)
  const [practiceUrl, setPracticeUrl] = useState<string | null>(null)
  const [activeTrack, setActiveTrack] = useState<'model' | 'recording'>('model')

  // 錄音 blob URL 只在換新錄音時手動 revoke 前一個（見 onstop）；離開頁面時最後
  // 一個還沒被 revoke，靠這個 ref + unmount cleanup 補上，避免每次進出練習模式
  // 累積一個 blob URL 洩漏。
  const practiceUrlRef = useRef<string | null>(null)
  useLayoutEffect(() => { practiceUrlRef.current = practiceUrl })
  useEffect(() => {
    return () => { if (practiceUrlRef.current) URL.revokeObjectURL(practiceUrlRef.current) }
  }, [])

  useEffect(() => {
    const el = new Audio()
    el.preload = 'auto'
    // preservesPitch 未進所有 TS lib/瀏覽器版本，連同 webkit 前綴一起設。
    // 跟 audioEngine.ts 內部 setPreservesPitch 同邏輯，避免 0.5x 變 chipmunk。
    const anyEl = el as HTMLAudioElement & { preservesPitch?: boolean; webkitPreservesPitch?: boolean }
    anyEl.preservesPitch = true
    anyEl.webkitPreservesPitch = true
    appendToHost(el)
    practiceElRef.current = el
    return () => {
      // 離開 route：remove element 避免 unlock state leak、釋放記憶體
      el.pause()
      el.removeAttribute('src')
      el.remove()
      practiceElRef.current = null
    }
  }, [])

  // 切換 activeTrack 時，recording 的 src 要綁 practiceUrl
  useEffect(() => {
    const el = practiceElRef.current
    if (!el) return
    if (activeTrack === 'recording' && practiceUrl) {
      if (!el.src.includes(practiceUrl)) el.src = practiceUrl
    } else if (activeTrack === 'model') {
      // 切回 model：保留 recording element 的 src（user 可隨時切回 A），但暫停它
      el.pause()
    }
  }, [activeTrack, practiceUrl])

  // 錄音狀態
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const [isRecording, setIsRecording] = useState(false)
  const [hasRecording, setHasRecording] = useState(false)

  const startRecording = useCallback(async () => {
    // iOS Safari：麥克風權限也要 user gesture 才能拿到
    if (!navigator.mediaDevices?.getUserMedia) {
      toast.error('此裝置不支援錄音')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const rec = new MediaRecorder(stream)
      chunksRef.current = []
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' })
        const url = URL.createObjectURL(blob)
        setPracticeUrl((prev) => { if (prev) URL.revokeObjectURL(prev); return url })
        setHasRecording(true)
      }
      rec.start()
      recorderRef.current = rec
      setIsRecording(true)
    } catch (err) {
      console.error('[practice] getUserMedia failed', err)
      toast.error('麥克風權限被拒，請到設定開啟')
    }
  }, [])

  const stopRecording = useCallback(() => {
    const rec = recorderRef.current
    if (!rec || rec.state === 'inactive') return
    rec.stop()
    recorderRef.current = null
    setIsRecording(false)
  }, [])

  const playPractice = useCallback(() => {
    const el = practiceElRef.current
    if (!el || !practiceUrl) return
    el.currentTime = 0
    void el.play().catch((err) => {
      console.error('[practice] practiceEl play() rejected', err)
      toast.error('播放錄音失敗')
    })
  }, [practiceUrl])

  const playCurrentSegment = useCallback(() => {
    // 重播：currentTime = 0 + play()。同一個 element，不重 fetch。
    if (activeTrack === 'recording') {
      playPractice()
      return
    }
    // model 路徑：靠既有 useSegmentPlayer.play()，會自動接段
    void play()
  }, [activeTrack, play, playPractice])

  // word click：跳到該字時間點（沿用 useSegmentPlayer.seekToWord）
  const handleWordSeek = useCallback((word: string, cue: Cue): boolean => {
    const cueIdx = episode?.cues.indexOf(cue) ?? -1
    if (cueIdx < 0 || !episode) return false
    const words = cue.words
    if (!words || words.length === 0) return false
    const idx = words.findIndex((w) => w.word === word)
    if (idx < 0) {
      seekTo(cue.start)
      return true
    }
    return seekToWord(cueIdx, idx)
  }, [episode, seekTo, seekToWord])

  if (fetchError) {
    return <ErrorBanner message={fetchError} onRetry={() => void reload()} retryLabel="重新載入" className="h-64" />
  }
  if (!episode) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-3">
        <Sparkles size={24} className="text-accent animate-pulse" />
        <p className="text-text-tertiary text-sm">載入中…</p>
      </div>
    )
  }

  return (
    <div className="bg-bg-canvas min-h-[100dvh] text-text-primary flex flex-col">
      {/* 頂部導航：練習模式 = 從 PlayerRoute 跳進來的層級，back 鍵回上一頁 */}
      <header className="sticky top-0 z-30 flex items-center gap-3 px-4 py-3 bg-bg-primary/70 backdrop-blur-xl border-b border-border/60">
        <button
          onClick={() => navigate(-1)}
          aria-label="返回上一頁"
          className="flex items-center justify-center w-10 h-10 rounded-full transition-transform duration-fast active:scale-95 hover:bg-bg-secondary"
        >
          <ChevronLeft size={22} />
        </button>
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-widest text-text-tertiary">練習模式</div>
          <h1 className="text-base font-semibold truncate">{episode.title}</h1>
        </div>
      </header>

      {/* 字幕區：重用 LyricsView，把 word click 接到 seekToWord */}
      <main className="flex-1 min-h-0 overflow-y-auto pb-[260px]">
        <LyricsView
          episodeId={episode.id}
          episodeTitle={episode.title}
          cues={episode.cues}
          currentTime={currentTime}
          onWordClick={() => { /* 練習模式不開字典 */ }}
          onCueClick={(cue) => seekTo(cue.start)}
          onWordSeek={handleWordSeek}
          references={episode.references}
        />
      </main>

      {/* 底部控制列：sticky、translucent material */}
      <footer className="fixed bottom-0 left-0 right-0 z-30 px-4 pb-6 pt-4 bg-bg-primary/70 backdrop-blur-xl border-t border-border/60">
        {/* A/B track toggle */}
        <div className="flex items-center justify-center gap-2 mb-3">
          <button
            onClick={() => setActiveTrack('model')}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-all duration-fast active:scale-95 ${
              activeTrack === 'model'
                ? 'bg-accent text-white shadow-sm'
                : 'bg-bg-secondary text-text-secondary hover:bg-bg-secondary/80'
            }`}
            aria-pressed={activeTrack === 'model'}
          >
            Model
          </button>
          <button
            onClick={() => setActiveTrack('recording')}
            disabled={!hasRecording}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-all duration-fast active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed ${
              activeTrack === 'recording'
                ? 'bg-accent text-white shadow-sm'
                : 'bg-bg-secondary text-text-secondary hover:bg-bg-secondary/80'
            }`}
            aria-pressed={activeTrack === 'recording'}
          >
            我的錄音
          </button>
        </div>

        {/* Speed toggle (5 檔) */}
        <div className="flex items-center justify-center gap-1 mb-3">
          {PRACTICE_RATES.map((r) => (
            <button
              key={r}
              onClick={() => setPlaybackRate(r)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-all duration-fast active:scale-95 ${
                playbackRate === r
                  ? 'bg-accent text-white'
                  : 'bg-bg-secondary text-text-secondary hover:bg-bg-secondary/80'
              }`}
              aria-pressed={playbackRate === r}
            >
              {r}x
            </button>
          ))}
        </div>

        {/* 主控制列：Repeat + Record/Stop */}
        <div className="flex items-center justify-center gap-6">
          <button
            onClick={playCurrentSegment}
            disabled={activeTrack === 'recording' && !hasRecording}
            aria-label="重播這一句"
            className="flex items-center justify-center w-14 h-14 rounded-full bg-bg-secondary hover:bg-bg-secondary/80 transition-transform duration-fast active:scale-95 disabled:opacity-40"
          >
            <Repeat size={22} />
          </button>
          {isRecording ? (
            <button
              onClick={stopRecording}
              aria-label="停止錄音"
              className="flex items-center justify-center w-16 h-16 rounded-full bg-red-500 text-white shadow-lg transition-transform duration-fast active:scale-95"
            >
              <Square size={22} fill="currentColor" />
            </button>
          ) : (
            <button
              onClick={() => void startRecording()}
              aria-label="開始錄音"
              className="flex items-center justify-center w-16 h-16 rounded-full bg-accent text-white shadow-lg transition-transform duration-fast active:scale-95"
            >
              <Mic size={24} />
            </button>
          )}
          {hasRecording && !isRecording && (
            <button
              onClick={playPractice}
              aria-label="播放錄音"
              className="flex items-center justify-center w-14 h-14 rounded-full bg-bg-secondary hover:bg-bg-secondary/80 transition-transform duration-fast active:scale-95"
            >
              <Repeat1 size={22} />
            </button>
          )}
        </div>
        <div className="mt-2 text-center text-xs text-text-tertiary">
          {practiceStatusLabel(isRecording, activeTrack === 'recording' && hasRecording, isPlaying, playbackRate)}
        </div>
      </footer>
    </div>
  )
}