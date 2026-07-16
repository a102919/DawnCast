import { Volume2 } from 'lucide-react'

export interface PronounceButtonProps {
  readonly audioUrl: string | null | undefined
  readonly size?: number
}

/** 發音按鈕：無音檔時不渲染。詞卡（WordCardPanel）與單字本卡片（VocabEntryCard）共用。 */
export function PronounceButton({ audioUrl, size = 14 }: PronounceButtonProps) {
  if (!audioUrl) return null
  return (
    <button
      type="button"
      onClick={e => { e.stopPropagation(); void new Audio(audioUrl).play() }}
      aria-label="播放單字發音"
      className="text-text-tertiary hover:text-accent transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
    >
      <Volume2 size={size} />
    </button>
  )
}
