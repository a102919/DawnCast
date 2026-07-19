import { Volume2 } from 'lucide-react'

export interface PronounceButtonProps {
  readonly audioUrl: string | null | undefined
  /** 無 audioUrl 時的 TTS 內容（單字或例句） */
  readonly text: string | null | undefined
  readonly size?: number
  readonly label?: string
}

// ponytail: 沒有 audioUrl 就用瀏覽器內建 Web Speech API 唸，不用另外產音檔
function speak(text: string): void {
  window.speechSynthesis.cancel()
  const utter = new SpeechSynthesisUtterance(text)
  utter.lang = 'en-US'
  window.speechSynthesis.speak(utter)
}

/** 發音按鈕：有 audioUrl 播音檔，否則用裝置內建語音朗讀 text。詞卡與單字本卡片共用。 */
export function PronounceButton({ audioUrl, text, size = 14, label = '播放發音' }: PronounceButtonProps) {
  if (!audioUrl && !text) return null
  return (
    <button
      type="button"
      onClick={e => {
        e.stopPropagation()
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
