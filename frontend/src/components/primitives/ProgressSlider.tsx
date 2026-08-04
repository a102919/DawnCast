import { computeProgress } from '../../lib/playback'

interface ProgressSliderProps {
  readonly currentTime: number
  readonly duration: number
  readonly onSeek: (time: number) => void
  /** 外層 wrapper 額外 class（例：行動版 bottom bar 需要 flex-1 撐滿列寬）。 */
  readonly className?: string
}

/** 播放進度條：PlayerControls（桌面）與 PlayerBottomBar（行動）共用。
 *
 * controlled range input，進度用 linear-gradient 畫出已播放／未播放兩段色塊，
 * 取代原生 thumb track 樣式；aria-valuemin/max/now 讓螢幕閱讀器報得出目前進度。 */
export function ProgressSlider({ currentTime, duration, onSeek, className = '' }: ProgressSliderProps) {
  const progress = computeProgress(currentTime, duration)

  return (
    <div className={`relative py-4 -my-4 ${className}`}>
      <input
        type="range"
        min={0}
        max={duration}
        value={currentTime}
        step={0.1}
        onChange={e => onSeek(Number(e.target.value))}
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
  )
}
