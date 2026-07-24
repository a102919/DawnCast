import { useState } from 'react'
import { Lightbulb, ChevronDown } from 'lucide-react'

interface MnemonicHintProps {
  readonly text: string
}

/** 諧音/關鍵字記憶提示：預設收合、次要視覺權重。
 *
 * 刻意不跟主要翻譯/IPA/發音平起平坐、也不預設展開——研究顯示諧音法只是
 * 初學者的暫時拐杖，最終要靠語境提取練習才能建立主動字彙，不該讓使用者養成永久依賴。
 */
export function MnemonicHint({ text }: MnemonicHintProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded(e => !e)}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-text-tertiary hover:text-text-secondary transition-colors duration-fast"
      >
        <Lightbulb size={12} />
        記憶小提示
        <ChevronDown size={12} className={`transition-transform duration-fast ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && (
        <p className="mt-1.5 text-xs text-text-tertiary leading-relaxed">{text}</p>
      )}
    </div>
  )
}
