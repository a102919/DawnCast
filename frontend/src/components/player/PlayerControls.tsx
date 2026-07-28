import { useEffect, useCallback, useRef } from 'react'
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
  const { currentTime, isPlaying, seekTo, play, pause, playbackRate, setPlaybackRate, volume, setVolume } = usePlayer()
  // mute 是「上次音量記憶」UI 概念：toggle 把 volume 在 0 ↔ prevVolume 間切換。
  // hook 內 volume=0 等於 mute（segmentGain.gain=0），所以實際行為直接看 hook.volume。
  const lastNonZeroVolumeRef = useRef(1)
  const isMuted = volume === 0

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value)
    if (val > 0) lastNonZeroVolumeRef.current = val
    setVolume(val)
  }

  const toggleMute = useCallback(() => {
    if (isMuted) setVolume(lastNonZeroVolumeRef.current || 0.7)
    else { lastNonZeroVolumeRef.current = volume; setVolume(0) }
  }, [isMuted, volume, setVolume])

  // 鍵盤快捷鍵
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName.toLowerCase()
      if (tag === 'input' || tag === 'textarea') return

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
  }, [isPlaying, currentTime, duration, play, pause, seekTo])

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
          <div className="flex items-center gap-1.5">
            <IconButton label={isMuted ? '取消靜音' : '靜音'} onClick={toggleMute}>
              {isMuted ? <VolumeX size={16} /> : <Volume2 size={16} />}
            </IconButton>
            <div className="hidden sm:flex items-center py-4 -my-4">
              <input
                type="range"
                min={0}
                max={1}
                step={0.02}
                value={isMuted ? 0 : volume}
                onChange={handleVolumeChange}
                className="w-20 h-1 accent-accent cursor-pointer"
                aria-label="音量"
              />
            </div>
          </div>
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
