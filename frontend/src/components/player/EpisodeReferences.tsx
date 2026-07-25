import { BookMarked, ExternalLink, ChevronDown } from 'lucide-react'
import type { SourceReference } from '../../types/episode'

interface EpisodeReferencesProps {
  readonly references: readonly SourceReference[]
}

/** 播放器底部參考資料／延伸閱讀（Task #67）。
 *
 * 設計原則：
 *  - 「無來源不渲染」：傳入空陣列或 undefined 直接回 null，不留空白框。
 *  - 原生 `<details>` 當作收合開關：不需要額外 JS state、SSR 友善、鍵盤可達。
 *  - 外連一律 `target="_blank" rel="noopener noreferrer"` 防止 tabnabbing、
 *    反向 referrer 洩漏；用 lucide `ExternalLink` icon 標示是外部連結。
 *  - summary 預設關閉：聽完想看再點，避免打擾主要播放體驗。
 */
export function EpisodeReferences({ references }: EpisodeReferencesProps) {
  if (references.length === 0) return null

  return (
    <details
      className="group rounded-lg border border-border bg-bg-primary/60 backdrop-blur-sm text-left"
    >
      <summary
        className="flex items-center gap-2 px-3 py-2 cursor-pointer list-none select-none text-sm text-text-secondary hover:text-text-primary transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-lg"
      >
        <BookMarked size={14} className="shrink-0 text-accent" aria-hidden="true" />
        <span className="font-medium">參考資料</span>
        <span className="text-xs text-text-tertiary">（{references.length}）</span>
        <ChevronDown
          size={14}
          className="ml-auto shrink-0 transition-transform duration-200 ease-apple group-open:rotate-180"
          aria-hidden="true"
        />
      </summary>
      <ul className="px-3 pb-3 pt-1 space-y-2">
        {references.map((ref, i) => (
          <li key={`${ref.url}-${i}`} className="text-sm">
            <a
              href={ref.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-start gap-1.5 text-accent hover:text-accent-hover hover:underline underline-offset-2 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
            >
              <span className="break-all">{ref.title}</span>
              <ExternalLink size={12} className="shrink-0 mt-0.5" aria-hidden="true" />
            </a>
            {ref.publisher && (
              <div className="text-xs text-text-tertiary mt-0.5 ml-0.5">
                {ref.publisher}
              </div>
            )}
          </li>
        ))}
      </ul>
    </details>
  )
}
