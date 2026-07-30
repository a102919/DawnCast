import { useState } from 'react'
import { Sparkles, Newspaper, MessageSquare, BookOpen, Clock, Timer, Hourglass } from 'lucide-react'
import { Button, Chip } from '../primitives'
import { TOPIC_LABELS } from '../../lib'
import type { TopicKey } from '../../lib'
import type { EntryMode, LengthTier } from '../../api'

type TopicChoice = Exclude<TopicKey, 'all'>

const TOPIC_ORDER: readonly TopicChoice[] = ['tech', 'business', 'culture', 'science'] as const

// Phase 4：使用者入口類型（與後端 EntryMode Literal 對齊；skill 不開給使用者）。
const ENTRY_MODES: readonly EntryMode[] = ['news', 'topic', 'knowledge'] as const

// 三入口的顯示中文字 + icon + 描述（icon 從 lucide-react 抓，不直接用 emoji）。
type IconComponent = typeof Newspaper
const ENTRY_MODE_META: Record<EntryMode, { label: string; hint: string; Icon: IconComponent }> = {
  news: {
    label: '今日新聞',
    hint: '系統抓當日新聞寫成單人口白快訊',
    Icon: Newspaper,
  },
  topic: {
    label: '指定主題',
    hint: '你想學什麼主題就寫什麼，自由度最高',
    Icon: MessageSquare,
  },
  knowledge: {
    label: '深度知識',
    hint: '維基百科等級的長篇解說，預設 15-20 分鐘',
    Icon: BookOpen,
  },
  // skill 對齊後端保留值；前端 UI 不會顯示，但型別完整性保留。
  skill: { label: '技能', hint: '', Icon: BookOpen },
}

// 長度 tier 顯示設定（與後端 LengthTier 對齊；時長字串從 _LENGTH_TIERS 抽的近似值）。
const LENGTH_TIERS: readonly LengthTier[] = ['short', 'medium', 'long'] as const
const LENGTH_TIER_META: Record<LengthTier, { label: string; duration: string; Icon: IconComponent }> = {
  short: { label: '短篇', duration: '2-3 分鐘', Icon: Timer },
  medium: { label: '中篇', duration: '6-8 分鐘', Icon: Clock },
  long: { label: '長篇', duration: '15-20 分鐘', Icon: Hourglass },
}

function defaultLengthFor(entryMode: EntryMode): LengthTier {
  // 入口與長度的預設對應（使用者可手動覆蓋）。
  if (entryMode === 'news') return 'short'
  if (entryMode === 'knowledge') return 'long'
  return 'medium'
}

export interface DailyOrderFormSubmitResult {
  readonly selectedTopics: readonly TopicChoice[]
  readonly specificRequest: string
  readonly entryMode: EntryMode
  readonly lengthTier: LengthTier
}

interface DailyOrderFormProps {
  readonly busy: boolean
  readonly onSubmit: (result: DailyOrderFormSubmitResult) => void
}

/** 純建單表單：包在 Sheet 裡使用，每次打開都是全新一份，沒有「編輯既有訂單」
 *  這件事——送出即觸發生成，狀態機推進只能由後端控制。 */
export function DailyOrderForm({ busy, onSubmit }: DailyOrderFormProps) {
  const [topics, setTopics] = useState<readonly TopicChoice[]>([])
  const [request, setRequest] = useState('')
  const [entryMode, setEntryMode] = useState<EntryMode>('topic')
  const [lengthTier, setLengthTier] = useState<LengthTier>('medium')
  // 切 entryMode 時，若使用者尚未明確覆寫長度，沿用新模式的預設長度。
  const [lengthTierTouched, setLengthTierTouched] = useState(false)

  const toggleTopic = (key: TopicChoice) => {
    setTopics(prev => (prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]))
  }

  const handleEntryModeChange = (next: EntryMode) => {
    setEntryMode(next)
    if (!lengthTierTouched) setLengthTier(defaultLengthFor(next))
  }

  const handleLengthTierChange = (next: LengthTier) => {
    setLengthTier(next)
    setLengthTierTouched(true)
  }

  const handleSubmit = () => {
    onSubmit({ selectedTopics: topics, specificRequest: request.trim(), entryMode, lengthTier })
  }

  const canSubmit = topics.length > 0

  return (
    <div className="p-5 space-y-5">
      <header className="space-y-1">
        <h2
          id="daily-order-sheet-title"
          className="text-headline tracking-headline leading-headline font-semibold text-text-primary"
        >
          新增一集
        </h2>
        <p className="text-caption leading-caption text-text-secondary">
          送出後立即開始生成，無法再修改。
        </p>
      </header>

      {/* Phase 4：三分頁入口選擇 */}
      <div className="space-y-2">
        <div className="text-xs font-medium text-text-tertiary">入口</div>
        <div className="flex gap-1.5 flex-wrap">
          {ENTRY_MODES.map(m => {
            const meta = ENTRY_MODE_META[m]
            const Icon = meta.Icon
            return (
              <Chip key={m} active={entryMode === m} onClick={() => handleEntryModeChange(m)}>
                <span className="inline-flex items-center gap-1.5">
                  <Icon size={14} aria-hidden />
                  {meta.label}
                </span>
              </Chip>
            )
          })}
        </div>
        <p className="text-[11px] text-text-tertiary">{ENTRY_MODE_META[entryMode].hint}</p>
      </div>

      {/* Phase 4：長度 tier 選擇器 */}
      <div className="space-y-2">
        <div className="text-xs font-medium text-text-tertiary">長度</div>
        <div className="flex gap-1.5 flex-wrap">
          {LENGTH_TIERS.map(t => {
            const meta = LENGTH_TIER_META[t]
            const Icon = meta.Icon
            return (
              <Chip key={t} active={lengthTier === t} onClick={() => handleLengthTierChange(t)}>
                <span className="inline-flex items-center gap-1.5">
                  <Icon size={14} aria-hidden />
                  {meta.label}
                  <span className="text-[10px] text-text-tertiary">· {meta.duration}</span>
                </span>
              </Chip>
            )
          })}
        </div>
      </div>

      {/* 主題 chips */}
      <div className="space-y-2" aria-required="true">
        <div className="text-xs font-medium text-text-tertiary">
          主題 <span className="text-warning" aria-hidden>*</span>
        </div>
        <div className="flex gap-1.5 flex-wrap">
          {TOPIC_ORDER.map(key => (
            <Chip key={key} active={topics.includes(key)} onClick={() => toggleTopic(key)}>
              {TOPIC_LABELS[key]}
            </Chip>
          ))}
        </div>
        {topics.length === 0 && (
          <p className="text-[11px] text-warning" role="status" aria-live="polite">
            至少選一個主題，才能送出。
          </p>
        )}
      </div>

      {/* 指定內容 */}
      <div className="space-y-2">
        <label htmlFor="daily-request" className="text-xs font-medium text-text-tertiary block">
          想特別學的內容 <span className="text-text-tertiary/70">（選填）</span>
        </label>
        <textarea
          id="daily-request"
          value={request}
          onChange={e => setRequest(e.target.value)}
          placeholder="例如：科技面試常見問答、餐廳點餐用語..."
          rows={3}
          className="w-full px-3 py-2.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-tertiary resize-none focus:outline-none focus:border-accent transition-colors duration-fast"
        />
      </div>

      {/* 操作列 */}
      <div className="flex justify-end pt-1">
        <Button
          onClick={handleSubmit}
          disabled={!canSubmit || busy}
          title={canSubmit ? undefined : '請先選至少一個主題'}
          aria-label={canSubmit ? undefined : '送出（請先選主題）'}
          size="md"
          variant="primary"
        >
          <Sparkles size={14} />
          送出
        </Button>
      </div>
    </div>
  )
}
