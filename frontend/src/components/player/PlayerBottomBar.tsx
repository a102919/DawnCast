import { RotateCcw, Play, Pause, ChevronRight, Repeat1, MessageCircle, BookMarked } from 'lucide-react'
import { IconButton, ProgressSlider } from '../primitives'
import { usePlayer } from '../../state'
import { formatTime } from '../../lib'
import { RATES } from '../../lib/playback'
import type { Cue } from '../../types/episode'

interface PlayerBottomBarProps {
  readonly duration: number
  readonly cues: readonly Cue[]
  readonly activeCueIdx: number
  readonly isCueLooping: boolean
  readonly canLoopCue: boolean
  readonly onCueLoopToggle: () => void
  readonly onNextCue: () => void
  readonly onCopyPrompt: () => void
  readonly onVocabOpen: () => void
  /** 重複聽的練習開關：關掉的那一層在歌詞裡變模糊佔位（見 LyricsView）。 */
  readonly showZh: boolean
  readonly showEn: boolean
  readonly onToggleZh: () => void
  readonly onToggleEn: () => void
}

/** 文字型工具鈕（速度、中/EN 開關共用）：44px 觸控目標，樣式對齊 IconButton。 */
const TEXT_BTN = 'inline-flex items-center justify-center min-h-[44px] px-1 text-xs font-medium rounded-md transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 hover:bg-bg-secondary'

export function PlayerBottomBar({
  duration,
  cues,
  activeCueIdx,
  isCueLooping,
  canLoopCue,
  onCueLoopToggle,
  onNextCue,
  onCopyPrompt,
  onVocabOpen,
  showZh,
  showEn,
  onToggleZh,
  onToggleEn,
}: PlayerBottomBarProps) {
  const { currentTime, isPlaying, seekTo, play, pause, playbackRate, setPlaybackRate } = usePlayer()

  const cycleRate = () => {
    const idx = RATES.findIndex(r => r === playbackRate)
    setPlaybackRate(RATES[(idx + 1) % RATES.length])
  }

  const handleRewind = () => seekTo(Math.max(0, currentTime - 10))

  return (
    <nav
      aria-label="播放工具列"
      className="lg:hidden fixed bottom-0 inset-x-0 material-thick border-t border-border z-40"
    >
      {/* Row 1：進度條 */}
      <div className="flex items-center gap-3 px-4 h-10">
        <span className="text-xs text-text-secondary font-mono w-[2.75rem] shrink-0 tabular-nums">
          {formatTime(currentTime)}
        </span>
        <ProgressSlider currentTime={currentTime} duration={duration} onSeek={seekTo} className="flex-1" />
        <span className="text-xs text-text-secondary font-mono w-[2.75rem] shrink-0 text-right tabular-nums">
          {formatTime(duration)}
        </span>
      </div>

      {/* Row 2：工具在左、播放控制靠右（單手拇指可及） */}
      <div className="h-14 flex items-center px-3">
        {/* 工具群組 */}
        <div className="flex items-center gap-1">
          <button
            onClick={cycleRate}
            aria-label={`播放速度 ${playbackRate} 倍，點擊切換`}
            className={`${TEXT_BTN} min-w-[44px] font-mono text-text-secondary hover:text-text-primary`}
          >
            {playbackRate}x
          </button>
          <div className="w-px h-6 bg-border mx-1 shrink-0" />
          {/* 中／EN 顯示開關：四種組合＝四種練法（雙語／只英文／只中文／全隱藏）。
              關閉狀態用刪除線＋淡色，一眼看得出「這層現在是藏起來的」。 */}
          <button
            onClick={onToggleZh}
            aria-pressed={showZh}
            aria-label={showZh ? '隱藏中文翻譯' : '顯示中文翻譯'}
            className={`${TEXT_BTN} min-w-[40px] ${showZh ? 'text-text-primary' : 'text-text-tertiary line-through'}`}
          >
            中
          </button>
          <button
            onClick={onToggleEn}
            aria-pressed={showEn}
            aria-label={showEn ? '隱藏英文字幕' : '顯示英文字幕'}
            className={`${TEXT_BTN} min-w-[40px] ${showEn ? 'text-text-primary' : 'text-text-tertiary line-through'}`}
          >
            EN
          </button>
          <IconButton label="複製英文對話練習 Prompt" onClick={onCopyPrompt}>
            <MessageCircle size={20} />
          </IconButton>
          <IconButton label="我的單字本" onClick={onVocabOpen}>
            <BookMarked size={20} />
          </IconButton>
        </div>

        {/* 分隔線 */}
        <div className="w-px h-6 bg-border mx-2 shrink-0" />

        {/* 播放控制群組（靠右） */}
        <div className="flex items-center gap-1 flex-1 justify-end">
          <IconButton label="倒退 10 秒" onClick={handleRewind}>
            <RotateCcw size={18} />
          </IconButton>

          <button
            aria-label={isPlaying ? '暫停' : '播放'}
            onClick={isPlaying ? pause : play}
            className="w-11 h-11 rounded-full bg-accent hover:bg-accent-hover active:scale-95 flex items-center justify-center shrink-0 transition-all duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
          >
            {isPlaying
              ? <Pause size={20} fill="white" color="white" />
              : <Play size={20} fill="white" color="white" className="translate-x-px" />}
          </button>

          <IconButton
            label={isCueLooping ? '關閉單句循環' : '開啟單句循環'}
            onClick={onCueLoopToggle}
            disabled={!canLoopCue}
            aria-pressed={isCueLooping}
            className={isCueLooping ? 'text-accent bg-accent/10' : ''}
          >
            <Repeat1 size={18} />
          </IconButton>

          <IconButton
            label="下一句"
            onClick={onNextCue}
            disabled={activeCueIdx < 0 || activeCueIdx >= cues.length - 1}
          >
            <ChevronRight size={18} />
          </IconButton>
        </div>
      </div>

      <div className="h-[env(safe-area-inset-bottom,0px)]" />
    </nav>
  )
}
