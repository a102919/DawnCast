import { Volume2 } from 'lucide-react'
import { usePlayer } from '../../state'

export interface PronounceButtonProps {
  readonly audioUrl: string | null | undefined
  /** 無 audioUrl 時的 TTS 內容（單字或例句） */
  readonly text: string | null | undefined
  /** 優先 player.playSegment 抽樣：傳入該字所在的 cue + 字在 cue 內的秒偏移。
   *  沒給則 fall back 到 audioUrl / Web Speech 路徑。 */
  readonly playSegmentRequest?: {
    readonly cueIdx: number
    readonly offsetSec: number
    readonly durationSec?: number
  }
  readonly size?: number
  readonly label?: string
}

// ponytail: 沒有 audioUrl 與 player 抽樣時，用瀏覽器內建 Web Speech API 唸。
function speak(text: string): void {
  window.speechSynthesis.cancel()
  const utter = new SpeechSynthesisUtterance(text)
  utter.lang = 'en-US'
  window.speechSynthesis.speak(utter)
}

/** 發音按鈕：優先用 player.playSegment 從該行 mp3 抽樣播（ducking 主音）；fallback 舊音檔；最終走 Web Speech。
 * 詞卡與單字本卡片共用。 */
export function PronounceButton({ audioUrl, text, playSegmentRequest, size = 14, label = '播放發音' }: PronounceButtonProps) {
  const player = usePlayer()
  if (!audioUrl && !text && !playSegmentRequest) return null
  return (
    <button
      type="button"
      onClick={e => {
        e.stopPropagation()
        // 優先：從該行 mp3 抽樣播（對齊 cue，跟 segment playback 完全一致）
        if (playSegmentRequest) {
          player.playSegment(
            playSegmentRequest.cueIdx,
            playSegmentRequest.offsetSec,
            playSegmentRequest.durationSec ?? 0.6,
          )
          return
        }
        if (audioUrl) void new Audio(audioUrl).play()
        else if (text) speak(text)
      }}
      aria-label={label}
      className="text-text-tertiary hover:text-accent transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
    >
      <Volume2 size={size} />
    </button>
  )
}
