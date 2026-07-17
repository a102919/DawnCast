import { useState } from 'react'
import {
  Sparkles,
  Trash2,
  CheckCircle2,
  Send,
  Lock,
  AlertTriangle,
  Clock,
  ChevronDown,
  SlidersHorizontal,
  Newspaper,
  MessageSquare,
  BookOpen,
  Timer,
  Hourglass,
} from 'lucide-react'
import { Button, Chip } from '../primitives'
import { TOPIC_LABELS, formatDateZhTW } from '../../routes/episodeData'
import type { TopicKey } from '../../routes/episodeData'
import type { DailyOrder, EntryMode, LengthTier } from '../../api'
import {
  DELIVERY_TIME_OPTIONS,
  formatCountdown,
  isOrderLocked,
  isPast,
  isToday,
  msUntilLock,
  getWeekdayLabel,
} from '../../lib/dailyOrderDate'

type TopicChoice = Exclude<TopicKey, 'all'>

const VALID_TOPICS: readonly TopicChoice[] = ['tech', 'business', 'culture', 'science'] as const

function isTopicChoice(s: string): s is TopicChoice {
  return (VALID_TOPICS as readonly string[]).includes(s)
}

const TOPIC_ORDER: readonly TopicChoice[] = ['tech', 'business', 'culture', 'science'] as const

// Phase 4：使用者入口類型（與後端 EntryMode Literal 對齊；skill 不開給使用者）。
const ENTRY_MODES: readonly EntryMode[] = ['news', 'topic', 'knowledge'] as const

function isEntryMode(s: string | undefined): s is EntryMode {
  return s === 'news' || s === 'topic' || s === 'knowledge' || s === 'skill'
}

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

export type DailyOrderFormSubmitResult =
  | {
      kind: 'submit'
      selectedTopics: readonly TopicChoice[]
      specificRequest: string
      deliveryTime: string
      entryMode: EntryMode
      lengthTier: LengthTier
    }
  | {
      kind: 'update'
      selectedTopics: readonly TopicChoice[]
      specificRequest: string
      deliveryTime: string
      entryMode: EntryMode
      lengthTier: LengthTier
    }
  | { kind: 'cancel' }

interface DailyOrderFormProps {
  readonly date: string
  readonly existing: DailyOrder | null
  readonly onSubmit: (result: DailyOrderFormSubmitResult) => void
  readonly busy: boolean
  readonly collapsed: boolean
  readonly onExpand: () => void
  /** 從 settings 來的預設出餐時間,新訂單帶入；既有訂單優先讀 existing.deliveryTime */
  readonly defaultDeliveryTime: string
}

export function DailyOrderForm({
  date,
  existing,
  onSubmit,
  busy,
  collapsed,
  onExpand,
  defaultDeliveryTime,
}: DailyOrderFormProps) {
  const locked = existing ? isOrderLocked(existing) : false
  const isDateInPast = isPast(date)
  const isDateToday = isToday(date)

  const [topics, setTopics] = useState<readonly TopicChoice[]>(() => initialTopics(existing))
  const [request, setRequest] = useState<string>(() => existing?.specificRequest ?? '')
  const [deliveryTime, setDeliveryTime] = useState<string>(
    () => existing?.deliveryTime ?? defaultDeliveryTime,
  )
  // Phase 4 新增：入口類型與長度 tier。existing 沒帶時是 undefined（舊 localStorage），
  // 這時依現況退回 'topic' / 'medium'；切換 entryMode 不會自動覆寫 lengthTier，
  // 避免覆蓋使用者已手動選的值，僅在「使用者從未動過 lengthTier」時補預設值。
  const [entryMode, setEntryMode] = useState<EntryMode>(() =>
    existing && isEntryMode(existing.entryMode) ? existing.entryMode : 'topic',
  )
  const [lengthTier, setLengthTier] = useState<LengthTier>(() => {
    // 既有訂單有記錄 → 用既有值；沒有 → 依 entryMode 預設。
    if (
      existing &&
      (existing.lengthTier === 'short' ||
        existing.lengthTier === 'medium' ||
        existing.lengthTier === 'long')
    ) {
      return existing.lengthTier
    }
    return defaultLengthFor(existing && isEntryMode(existing.entryMode) ? existing.entryMode : 'topic')
  })
  // 切 entryMode 時，若使用者尚未明確覆寫長度，沿用新模式的預設長度。
  // 用 lengthTierTouched state 區分「從未動過」vs「動過」。
  const [lengthTierTouched, setLengthTierTouched] = useState<boolean>(
    () => !!existing?.lengthTier,
  )
  // 「指定內容 + 出餐時間」折進進階區塊,預設收合
  const [advancedOpen, setAdvancedOpen] = useState<boolean>(false)

  const toggleTopic = (key: TopicChoice) => {
    if (locked) return
    setTopics(prev => (prev.includes(key) ? prev.filter(k => k !== key) : [...prev, key]))
  }

  const handleEntryModeChange = (next: EntryMode) => {
    if (locked) return
    setEntryMode(next)
    if (!lengthTierTouched) {
      setLengthTier(defaultLengthFor(next))
    }
  }

  const handleLengthTierChange = (next: LengthTier) => {
    if (locked) return
    setLengthTier(next)
    setLengthTierTouched(true)
  }

  const handlePrimary = () => {
    const payload = {
      selectedTopics: topics,
      specificRequest: request.trim(),
      deliveryTime,
      entryMode,
      lengthTier,
    }
    if (existing) {
      onSubmit({ kind: 'update', ...payload })
    } else {
      onSubmit({ kind: 'submit', ...payload })
    }
  }

  const handleCancel = () => onSubmit({ kind: 'cancel' })

  const canSubmit = topics.length > 0 && !locked && !isDateInPast

  // 摺疊狀態：顯示精簡摘要卡，點按鈕才展開編輯。
  // 元件實例不卸載,topics / request / deliveryTime 內部狀態得以保留。
  if (collapsed) {
    return (
      <CollapsedSummaryCard
        date={date}
        existing={existing}
        locked={locked}
        onExpand={onExpand}
      />
    )
  }

  return (
    <section className="p-5 rounded-xl border border-border bg-bg-primary space-y-5">
      <header className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-text-primary">
            {existing ? '編輯訂單' : isDateInPast ? '補點（過去）' : '新增訂單'}
          </h2>
          <StatusBadge existing={existing} locked={locked} />
        </div>
        <p className="text-xs text-text-secondary leading-relaxed">
          {isDateInPast
            ? '過去日期的訂單僅供查看，如要補點會建立新訂單。'
            : isDateToday
              ? '今天的餐可在送出後到「出餐前 6 小時」之前修改。'
              : '提前點餐可在送出後到「出餐前 6 小時」之前修改。'}
        </p>
      </header>

      {locked && <LockedBanner existing={existing} />}

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
      <div className="space-y-2">
        <div className="text-xs font-medium text-text-tertiary">主題</div>
        <div className="flex gap-1.5 flex-wrap">
          {TOPIC_ORDER.map(key => (
            <Chip
              key={key}
              active={topics.includes(key)}
              onClick={() => toggleTopic(key)}
            >
              {TOPIC_LABELS[key]}
            </Chip>
          ))}
        </div>
      </div>

      {/* 進階區塊觸發器:展開/收合指定內容 + 出餐時間。
          右側永遠顯示目前選到的出餐時間,讓使用者沒展開也不失憶。 */}
      <button
        type="button"
        onClick={() => setAdvancedOpen(o => !o)}
        aria-expanded={advancedOpen}
        aria-controls="daily-order-advanced"
        className="w-full flex items-center justify-between gap-2 px-3 py-2.5 rounded-md border border-dashed border-border bg-bg-secondary/40 text-xs text-text-secondary hover:border-accent/40 hover:text-text-primary transition-colors duration-fast min-h-[44px] disabled:opacity-50 disabled:cursor-not-allowed"
        disabled={locked}
      >
        <span className="inline-flex items-center gap-1.5">
          <SlidersHorizontal size={12} aria-hidden />
          {advancedOpen ? '收合進階選項' : '顯示進階選項'}
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="text-[11px] text-text-tertiary">出餐 {deliveryTime}</span>
          <ChevronDown
            size={14}
            aria-hidden
            className={`transition-transform duration-fast ${advancedOpen ? 'rotate-180' : ''}`}
          />
        </span>
      </button>

      {/* 進階區塊內容:指定內容 + 出餐時間 */}
      {advancedOpen && (
        <div id="daily-order-advanced" className="space-y-5" aria-hidden={!advancedOpen}>
          {/* 指定內容 */}
          <div className="space-y-2">
            <label
              htmlFor="daily-request"
              className="text-xs font-medium text-text-tertiary block"
            >
              想特別學的內容 <span className="text-text-tertiary/70">（選填）</span>
            </label>
            <textarea
              id="daily-request"
              value={request}
              onChange={e => setRequest(e.target.value)}
              placeholder="例如：科技面試常見問答、餐廳點餐用語..."
              rows={3}
              disabled={locked}
              className="w-full px-3 py-2.5 text-sm bg-bg-secondary border border-border rounded-md text-text-primary placeholder:text-text-tertiary resize-none focus:outline-none focus:border-accent transition-colors duration-fast disabled:opacity-50 disabled:cursor-not-allowed"
            />
          </div>

          {/* 出餐時間 chips */}
          <div className="space-y-2">
            <div className="text-xs font-medium text-text-tertiary flex items-center gap-1">
              <Clock size={12} aria-hidden />
              出餐時間
            </div>
            <div className="flex gap-1.5 flex-wrap">
              {DELIVERY_TIME_OPTIONS.map(opt => (
                <Chip
                  key={opt.value}
                  active={deliveryTime === opt.value}
                  onClick={() => !locked && setDeliveryTime(opt.value)}
                >
                  {opt.label}
                </Chip>
              ))}
            </div>
            {!isDateInPast && (
              <CountdownText existing={existing} deliveryTime={deliveryTime} date={date} />
            )}
          </div>
        </div>
      )}

      {/* 操作列 */}
      <div className="flex items-center justify-between gap-2 pt-1">
        {existing && !locked ? (
          <button
            type="button"
            onClick={handleCancel}
            disabled={busy}
            className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-danger disabled:opacity-40 disabled:cursor-not-allowed transition-colors duration-fast min-h-[44px] px-2"
          >
            <Trash2 size={12} />
            取消訂單
          </button>
        ) : (
          <span />
        )}

        <Button
          onClick={handlePrimary}
          disabled={!canSubmit || busy}
          size="md"
          variant="primary"
        >
          {existing ? (
            <>
              <Send size={14} />
              更新訂單
            </>
          ) : (
            <>
              <Sparkles size={14} />
              送出訂單
            </>
          )}
        </Button>
      </div>
    </section>
  )
}

function initialTopics(existing: DailyOrder | null): readonly TopicChoice[] {
  if (!existing) return []
  return existing.selectedTopics.filter(isTopicChoice)
}

function StatusBadge({
  existing,
  locked,
}: {
  readonly existing: DailyOrder | null
  readonly locked: boolean
}) {
  if (!existing) {
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full bg-bg-secondary text-text-tertiary border border-border">
        未點
      </span>
    )
  }
  if (existing.status === 'played') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-success/10 text-success border border-success/20">
        <CheckCircle2 size={10} />
        已播放
      </span>
    )
  }
  if (locked) {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/20">
        <Lock size={10} />
        已鎖定
      </span>
    )
  }
  if (existing.status === 'queued') {
    return (
      <span className="text-[10px] px-2 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
        已排入
      </span>
    )
  }
  return (
    <span className="text-[10px] px-2 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
      已送出
    </span>
  )
}

function LockedBanner({ existing }: { readonly existing: DailyOrder | null }) {
  if (existing?.status === 'played') {
    return (
      <div className="flex items-start gap-2 p-3 rounded-md bg-success/5 border border-success/20 text-xs text-text-secondary">
        <CheckCircle2 size={14} className="text-success shrink-0 mt-0.5" aria-hidden />
        <span>這一餐已播放過，無法再編輯。</span>
      </div>
    )
  }
  return (
    <div className="flex items-start gap-2 p-3 rounded-md bg-warning/5 border border-warning/20 text-xs text-text-secondary">
      <AlertTriangle size={14} className="text-warning shrink-0 mt-0.5" aria-hidden />
      <span>已過截止時間（出餐前 6 小時），這一餐無法再編輯。</span>
    </div>
  )
}

function CountdownText({
  existing,
  deliveryTime,
  date,
}: {
  readonly existing: DailyOrder | null
  readonly deliveryTime: string
  readonly date: string
}) {
  // 模擬一個 "現在訂單" 用來算截止倒數（既有的用 existing 的 deliveryTime，新的用當前 UI 選的）
  const synthetic = existing ?? {
    date,
    selectedTopics: [],
    status: 'pending' as const,
    deliveryTime,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  }
  const ms = msUntilLock({ ...synthetic, deliveryTime })
  const text = formatCountdown(ms)
  return (
    <div className="text-[11px] text-text-tertiary flex items-center gap-1">
      <Clock size={10} aria-hidden />
      截止倒數：{text}
    </div>
  )
}

// 摺疊狀態下的精簡摘要卡：一行日期 + 狀態 + 主題摘要 + 展開按鈕。
// 設計目標：歷史區塊可以一眼看到，使用者有需要再展開編輯。
function CollapsedSummaryCard({
  date,
  existing,
  locked,
  onExpand,
}: {
  readonly date: string
  readonly existing: DailyOrder | null
  readonly locked: boolean
  readonly onExpand: () => void
}) {
  const topicSummary = (existing?.selectedTopics ?? [])
    .map(t => TOPIC_LABELS[t as keyof typeof TOPIC_LABELS] ?? null)
    .filter((s): s is string => s !== null)
    .join('・') || '未指定主題'

  // Phase 4：把「入口・長度」加到摘要列；舊訂單沒帶欄位時不顯示而非出 undefined。
  const mode = isEntryMode(existing?.entryMode) ? existing.entryMode : null
  const tier =
    existing?.lengthTier === 'short' ||
    existing?.lengthTier === 'medium' ||
    existing?.lengthTier === 'long'
      ? existing.lengthTier
      : null
  const modeAndTier =
    mode && tier
      ? `${ENTRY_MODE_META[mode].label}・${LENGTH_TIER_META[tier].label}`
      : null

  return (
    <section className="rounded-xl border border-border bg-bg-primary">
      <div className="flex items-center gap-3 p-4">
        {/* 日期區塊 */}
        <div className="shrink-0 w-14 text-center">
          <div className="text-[10px] text-text-tertiary leading-none">星期{getWeekdayLabel(date)}</div>
          <div className="text-xl font-semibold text-text-primary leading-tight mt-0.5">
            {date.slice(8, 10)}
          </div>
        </div>

        {/* 狀態 + 摘要 */}
        <div className="flex-1 min-w-0 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-text-tertiary">{formatDateZhTW(date)}</span>
            <StatusBadge existing={existing} locked={locked} />
          </div>
          <div className="text-sm text-text-primary truncate">
            {existing ? (
              <>
                {modeAndTier && <span>{modeAndTier}</span>}
                {modeAndTier && <span className="text-text-tertiary"> · </span>}
                <span>{topicSummary}</span>
                <span className="text-text-tertiary"> · {existing.deliveryTime} 出餐</span>
              </>
            ) : (
              <span className="text-text-tertiary">還沒點餐</span>
            )}
          </div>
        </div>

        {/* 動作：鎖定時不顯示按鈕（檢視限定）；否則展開編輯 */}
        {locked ? (
          <Lock size={16} className="text-text-tertiary shrink-0" aria-hidden />
        ) : (
          <Button variant="ghost" size="sm" onClick={onExpand}>
            {existing ? '編輯' : '點餐'}
            <ChevronDown size={14} className="-mr-1" aria-hidden />
          </Button>
        )}
      </div>
    </section>
  )
}