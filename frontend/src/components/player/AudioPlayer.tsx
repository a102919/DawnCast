import { useEffect, useRef, useState } from 'react'
import { Headphones } from 'lucide-react'
import { usePlayer } from '../../state'

interface AudioPlayerProps {
  readonly audioUrl: string
}

/** Audio-only player：不顯示影片畫面，只用 <audio> 撐時間軸來源（cue 同步高亮）。
 *
 * Ponytail：player 層只負責 setVideoRef 餵 PlayerProvider；視覺外殼在 PlayerRoute 組合
 * （標題/封面/歌詞）。這元件刻意輸出極簡 — Apple Music 風的「看不到播放器，只有內容」感。
 */
export function AudioPlayer({ audioUrl }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const { setVideoRef, playbackRate } = usePlayer()
  const [hasError, setHasError] = useState(false)

  useEffect(() => {
    setVideoRef(audioRef.current)
    return () => setVideoRef(null)
  }, [setVideoRef])

  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = playbackRate
    }
  }, [playbackRate])

  return (
    <div className="relative">
      {hasError && (
        <div className="flex items-center justify-center gap-2 py-3 text-text-tertiary text-xs">
          <Headphones size={14} />
          <span>音檔載入失敗，請稍後再試</span>
        </div>
      )}
      <audio
        ref={audioRef}
        src={audioUrl}
        preload="metadata"
        onError={() => setHasError(true)}
        className="hidden"
      />
    </div>
  )
}
