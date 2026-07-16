import { useEffect, useCallback, useState, useRef } from 'react'
import { Play, Pause, Volume2, VolumeX } from 'lucide-react'
import { usePlayer } from '../../state'
import { formatTime } from '../../lib'
import { IconButton } from '../primitives'

interface PlayerControlsProps {
  readonly duration: number
}

const RATES = [0.75, 1, 1.25, 1.5] as const

export function PlayerControls({ duration }: PlayerControlsProps) {
  const { currentTime, isPlaying, seekTo, play, pause, playbackRate, setPlaybackRate, videoRef } = usePlayer()
  const [isMuted, setIsMuted] = useState(false)
  const volumeRef = useRef(1)
  const [volume, setVolumeState] = useState(1)
  const setVolume = (v: number) => { volumeRef.current = v; setVolumeState(v) }

  const handleSeek = (e: React.ChangeEvent<HTMLInputElement>) => {
    seekTo(Number(e.target.value))
  }

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value)
    setVolume(val)
    const video = videoRef.current
    if (video) {
      video.volume = val
      video.muted = val === 0
    }
    setIsMuted(val === 0)
  }

  const toggleMute = useCallback(() => {
    setIsMuted(prev => {
      const next = !prev
      const video = videoRef.current
      if (video) {
        video.muted = next
        if (!next && volumeRef.current === 0) {
          setVolume(0.7)
          video.volume = 0.7
        }
      }
      return next
    })
  }, [videoRef])

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

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0

  return (
    <div className="space-y-2">
      {/* 進度條 */}
      <div className="relative py-4 -my-4">
        <input
          type="range"
          min={0}
          max={duration}
          value={currentTime}
          step={0.1}
          onChange={handleSeek}
          aria-label="播放進度"
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={currentTime}
          className="w-full h-1 bg-border rounded-full appearance-none cursor-pointer accent-accent"
          style={{
            background: `linear-gradient(to right, var(--color-accent) ${progress}%, var(--color-border) ${progress}%)`,
          }}
        />
      </div>

      {/* 控制列 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <IconButton label={isPlaying ? '暫停' : '播放'} onClick={isPlaying ? pause : play}>
            {isPlaying ? <Pause size={18} /> : <Play size={18} />}
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
