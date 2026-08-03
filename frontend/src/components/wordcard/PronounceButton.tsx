import { useEffect, useRef, useState } from 'react'
import { Volume2 } from 'lucide-react'
import { speakWord } from '../../lib/speech'

/** 播音檔；載入或播放失敗走 onFail（退 TTS）。error 事件與 play() reject
 *  可能同時發生，settled 旗標保證 onFail 只觸發一次。
 *  audioRef 記著目前正在播的 Audio 元素：開播前先暫停上一個，避免快速連點
 *  同時有兩顆 Audio 疊音；元件卸載時 caller 也靠這個 ref 暫停掉還在播的音檔。 */
async function playAudio(
  url: string,
  audioRef: { current: HTMLAudioElement | null },
  onEnd: () => void,
  onFail: () => void,
): Promise<void> {
  audioRef.current?.pause()
  const audio = new Audio(url)
  audioRef.current = audio
  let settled = false
  const fail = () => {
    if (settled) return
    settled = true
    onFail()
  }
  audio.addEventListener('ended', () => {
    settled = true
    onEnd()
  })
  audio.addEventListener('error', fail)
  try {
    await audio.play()
  } catch {
    fail()
  }
}

export interface PronounceButtonProps {
  readonly audioUrl: string | null | undefined
  /** 無 audioUrl 時的 TTS 內容（單字或例句） */
  readonly text: string | null | undefined
  readonly size?: number
  readonly label?: string
}

/** 發音按鈕：優先字典音檔，無則走 Web Speech TTS。
 *  播放中 icon 轉 accent 色＋pulse；點擊範圍以 padding 外擴、負 margin 抵銷不動版面。
 *  詞卡與單字本卡片共用。 */
export function PronounceButton({ audioUrl, text, size = 14, label = '播放發音' }: PronounceButtonProps) {
  const [playing, setPlaying] = useState(false)
  // 每次播放發一個 id；舊播放的結束回呼若已被新播放取代（或元件卸載）就不動狀態
  const playIdRef = useRef(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  useEffect(() => () => {
    playIdRef.current += 1
    audioRef.current?.pause()
  }, [])

  if (!audioUrl && !text) return null

  const beginPlay = () => {
    playIdRef.current += 1
    const id = playIdRef.current
    setPlaying(true)
    return () => {
      if (playIdRef.current === id) setPlaying(false)
    }
  }

  return (
    <button
      type="button"
      onClick={e => {
        e.stopPropagation()
        const done = beginPlay()
        const tts = () => {
          if (text) speakWord(text, done)
          else done()
        }
        if (audioUrl) void playAudio(audioUrl, audioRef, done, tts)
        else tts()
      }}
      aria-label={label}
      className={`p-2 -m-2 rounded transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
        playing ? 'text-accent' : 'text-text-tertiary hover:text-accent'
      }`}
    >
      <Volume2 size={size} className={playing ? 'animate-pulse' : undefined} />
    </button>
  )
}
