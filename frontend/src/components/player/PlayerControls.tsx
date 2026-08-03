import { useEffect, useCallback, useLayoutEffect, useRef } from 'react'
import { Play, Pause, Repeat1, Volume2, VolumeX } from 'lucide-react'
import { usePlayer } from '../../state'
import { formatTime } from '../../lib'
import { RATES } from '../../lib/playback'
import { IconButton, ProgressSlider } from '../primitives'

interface PlayerControlsProps {
  readonly duration: number
  readonly isCueLooping: boolean
  readonly canLoopCue: boolean
  readonly onCueLoopToggle: () => void
}

export function PlayerControls({ duration, isCueLooping, canLoopCue, onCueLoopToggle }: PlayerControlsProps) {
  const { currentTime, isPlaying, seekTo, play, pause, playbackRate, setPlaybackRate, muted, setMuted } = usePlayer()

  const toggleMute = useCallback(() => { setMuted(!muted) }, [muted, setMuted])

  // 鍵盤快捷鍵：currentTime 每次 timeupdate（每秒多次）都會變，若放進 deps 會讓這個
  // effect 頻繁拆掉重掛 window keydown listener；改用 ref 讀最新值，effect 只掛一次。
  const latestRef = useRef({ isPlaying, currentTime, duration, play, pause, seekTo })
  useLayoutEffect(() => {
    latestRef.current = { isPlaying, currentTime, duration, play, pause, seekTo }
  })

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.target instanceof HTMLElement)) return
      const tag = e.target.tagName.toLowerCase()
      if (tag === 'input' || tag === 'textarea') return

      const { isPlaying, currentTime, duration, play, pause, seekTo } = latestRef.current
      if (e.code === 'Space') {
        e.preventDefault()
        if (isPlaying) pause()
        else play()
      } else if (e.code === 'ArrowLeft') {
        e.preventDefault()
        seekTo(Math.max(0, currentTime - 5))
      } else if (e.code === 'ArrowRight') {
        e.preventDefault()
        seekTo(Math.min(duration, currentTime + 5))
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  return (
    <div className="space-y-2">
      {/* 進度條 */}
      <ProgressSlider currentTime={currentTime} duration={duration} onSeek={seekTo} />

      {/* 控制列 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <IconButton label={isPlaying ? '暫停' : '播放'} onClick={isPlaying ? pause : play}>
            {isPlaying ? <Pause size={18} /> : <Play size={18} />}
          </IconButton>
          <IconButton
            label={isCueLooping ? '關閉單句循環' : '開啟單句循環'}
            onClick={onCueLoopToggle}
            disabled={!canLoopCue}
            aria-pressed={isCueLooping}
            className={isCueLooping ? 'text-accent bg-accent/10' : ''}
          >
            <Repeat1 size={18} />
          </IconButton>
          <IconButton label={muted ? '取消靜音' : '靜音'} onClick={toggleMute} aria-pressed={muted}>
            {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
          </IconButton>
        </div>

        <span className="text-xs text-text-secondary font-mono">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>

        <div className="flex items-center gap-1">
          {RATES.map(rate => (
            <button
              key={rate}
              onClick={() => setPlaybackRate(rate)}
              aria-pressed={playbackRate === rate}
              className={`inline-flex items-center justify-center text-xs px-2 min-h-[44px] min-w-[44px] rounded transition-colors duration-fast ease-apple ${
                playbackRate === rate
                  ? 'bg-accent text-white'
                  : 'text-text-secondary hover:text-text-primary hover:bg-bg-secondary'
              }`}
            >
              {rate}x
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
