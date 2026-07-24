import { RotateCcw, Play, Pause, ChevronRight, BookMarked, MessageCircle } from 'lucide-react'
import { IconButton } from '../primitives'
import { usePlayer } from '../../state'
import { formatTime } from '../../lib'
import type { Cue } from '../../types/episode'

const RATES = [0.75, 1, 1.25, 1.5] as const
type Rate = typeof RATES[number]

interface PlayerBottomBarProps {
  readonly duration: number
  readonly cues: readonly Cue[]
  readonly activeCueIdx: number
  readonly onCopyPrompt: () => void
  readonly onVocabOpen: () => void
}

export function PlayerBottomBar({
  duration,
  cues,
  activeCueIdx,
  onCopyPrompt,
  onVocabOpen,
}: PlayerBottomBarProps) {
  const { currentTime, isPlaying, seekTo, play, pause, playbackRate, setPlaybackRate } = usePlayer()

  const cycleRate = () => {
    const idx = RATES.indexOf(playbackRate as Rate)
    setPlaybackRate(RATES[(idx + 1) % RATES.length])
  }

  const handleRewind = () => seekTo(Math.max(0, currentTime - 10))

  const handleNextCue = () => {
    const next = cues[activeCueIdx + 1]
    if (next) seekTo(next.start)
  }

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0

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
        <div className="relative flex-1 py-4 -my-4">
          <input
            type="range"
            min={0}
            max={duration}
            value={currentTime}
            step={0.1}
            onChange={e => seekTo(Number(e.target.value))}
            aria-label="播放進度"
            className="w-full h-1 rounded-full appearance-none cursor-pointer"
            style={{
              background: `linear-gradient(to right, var(--color-accent) ${progress}%, var(--color-border) ${progress}%)`,
            }}
          />
        </div>
        <span className="text-xs text-text-secondary font-mono w-[2.75rem] shrink-0 text-right tabular-nums">
          {formatTime(duration)}
        </span>
      </div>

      {/* Row 2：播放控制 */}
      <div className="h-14 flex items-center px-3">
        {/* 播放控制群組（置中） */}
        <div className="flex items-center gap-1 flex-1 justify-center">
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
            label="下一句"
            onClick={handleNextCue}
            disabled={activeCueIdx < 0 || activeCueIdx >= cues.length - 1}
          >
            <ChevronRight size={18} />
          </IconButton>
        </div>

        {/* 分隔線 */}
        <div className="w-px h-6 bg-border mx-2 shrink-0" />

        {/* 工具群組 */}
        <div className="flex items-center gap-1">
          <button
            onClick={cycleRate}
            aria-label={`播放速度 ${playbackRate} 倍，點擊切換`}
            className="inline-flex items-center justify-center min-w-[44px] min-h-[44px] px-1 text-xs font-mono font-medium text-text-secondary hover:text-text-primary hover:bg-bg-secondary rounded-md transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
          >
            {playbackRate}x
          </button>
          <div className="w-px h-6 bg-border mx-1 shrink-0" />
          <IconButton label="我的單字本" onClick={onVocabOpen}>
            <BookMarked size={20} />
          </IconButton>
          <IconButton label="複製英文對話練習 Prompt" onClick={onCopyPrompt}>
            <MessageCircle size={20} />
          </IconButton>
        </div>
      </div>

      <div className="h-[env(safe-area-inset-bottom,0px)]" />
    </nav>
  )
}
