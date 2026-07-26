import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { ShieldCheck, Send, Eye, EyeOff, RefreshCw, ChevronDown, ChevronRight } from 'lucide-react'
import { api, AppError, clearAdminToken, getAdminToken, setAdminToken } from '../api'
import type { AdminEpsGenerateInput, AdminEpsGenerateResponse, AdminTokenUsageResponse } from '../api'
import { Button, Card, ErrorBanner, SectionLabel, StatCard } from '../components/primitives'

const ANGLE_OPTIONS = [
  { value: '定義', label: '定義' },
  { value: '人物故事', label: '人物故事' },
  { value: '常見誤解', label: '常見誤解' },
  { value: '應用場景', label: '應用場景' },
  { value: '歷史', label: '歷史' },
  { value: '對比', label: '對比' },
] as const

const TOPIC_TYPE_OPTIONS = [
  { value: 'evergreen', label: '常青（不受時效影響）' },
  { value: 'news', label: '時效新聞' },
  { value: 'product', label: '產品介紹' },
  { value: 'skill', label: '技能教學' },
] as const

const LENGTH_TIER_OPTIONS = [
  { value: 'short', label: '短（約 3 分鐘）' },
  { value: 'medium', label: '中（約 5–8 分鐘）' },
  { value: 'long', label: '長（約 10–12 分鐘）' },
] as const

const CEFR_OPTIONS = [
  { value: 'A2', label: 'A2 初級' },
  { value: 'B1', label: 'B1 中級' },
  { value: 'B2', label: 'B2 中高級' },
] as const

function todayIso(): string {
  // 後端 app_timezone 預設 Asia/Taipei；前端用本地時區顯示，差一天也只差 deliverDate
  // 預設值的字串，後端拿到後會做時區正規化（見 router 內 datetime.now(tz).date()）。
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function AdminRoute() {
  const [tokenDraft, setTokenDraft] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [token, setTokenState] = useState<string | null>(getAdminToken())
  const [topic, setTopic] = useState('')
  const [angle, setAngle] = useState<(typeof ANGLE_OPTIONS)[number]['value']>('應用場景')
  const [topicType, setTopicType] = useState<(typeof TOPIC_TYPE_OPTIONS)[number]['value']>('evergreen')
  const [lengthTier, setLengthTier] = useState<(typeof LENGTH_TIER_OPTIONS)[number]['value']>('medium')
  const [cefr, setCefr] = useState<(typeof CEFR_OPTIONS)[number]['value']>('B1')
  const [userIdsRaw, setUserIdsRaw] = useState('')
  const [deliverDate, setDeliverDate] = useState(todayIso())
  const [overrideDate, setOverrideDate] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastResult, setLastResult] = useState<AdminEpsGenerateResponse | null>(null)

  const handleSaveToken = () => {
    const trimmed = tokenDraft.trim()
    if (!trimmed) {
      toast.error('權杖不可為空')
      return
    }
    setAdminToken(trimmed)
    setTokenState(trimmed)
    setTokenDraft('')
    toast.success('已儲存管理員權杖')
  }

  const handleClearToken = () => {
    clearAdminToken()
    setTokenState(null)
    setTokenDraft('')
    toast.success('已清除管理員權杖')
  }

  const handleSubmit = async () => {
    if (!topic.trim()) {
      setError('主題不可為空')
      return
    }
    setBusy(true)
    setError(null)
    setLastResult(null)

    // userIds 逗號分隔解析；空白清單等同不指定（純公開）。
    const userIds = userIdsRaw
      .split(/[\s,]+/)
      .map(s => s.trim())
      .filter(s => s.length > 0)

    const input: AdminEpsGenerateInput = {
      topic: topic.trim(),
      angle,
      topicType,
      lengthTier,
      cefr,
      ...(userIds.length > 0 ? { userIds } : {}),
      ...(overrideDate ? { deliverDate } : {}),
    }

    try {
      const result = await api.triggerAdminGenerate(input)
      setLastResult(result)
      toast.success(`已入列 msgId=${result.msgId}`)
    } catch (err) {
      const msg = err instanceof AppError ? err.message : err instanceof Error ? err.message : '未知錯誤'
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-6">
      <header className="flex items-center gap-2">
        <ShieldCheck size={20} className="text-accent" />
        <h1 className="text-lg font-semibold text-text-primary">管理後台</h1>
      </header>

      {/* Token 設定區塊：未設定時要求填入，設定後可改 / 清除。 */}
      <Card className="p-4 space-y-3">
        <SectionLabel>管理員權杖</SectionLabel>
        {token ? (
          <div className="space-y-2">
            <p className="text-xs text-text-secondary">
              已設定。每次請求會帶 <code className="text-text-primary">X-Admin-Token</code> header 呼叫後端。
            </p>
            <div className="flex gap-2">
              <Button size="sm" variant="ghost" onClick={handleClearToken}>
                清除權杖
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-text-secondary">
              從 Zeabur 後台 <code className="text-text-primary">ADMIN_TOKEN</code> 環境變數複製貼上，存於 localStorage。
            </p>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <input
                  type={showToken ? 'text' : 'password'}
                  value={tokenDraft}
                  onChange={e => setTokenDraft(e.target.value)}
                  placeholder="貼上管理員權杖"
                  className="w-full px-3 py-2 pr-10 text-sm rounded-md border border-border bg-bg-primary text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  autoComplete="off"
                  spellCheck={false}
                />
                <button
                  type="button"
                  onClick={() => setShowToken(s => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text-secondary hover:text-text-primary"
                  aria-label={showToken ? '隱藏權杖' : '顯示權杖'}
                >
                  {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <Button variant="primary" size="md" onClick={handleSaveToken} disabled={!tokenDraft.trim()}>
                儲存
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* 觸發表單：topic 必填，其餘選項預設即可發。 */}
      <Card className="p-4 space-y-4">
        <SectionLabel>觸發單集生成</SectionLabel>
        <p className="text-xs text-text-secondary">
          落庫時 isFree=true（公開，登入即可見）。worker 處理約 5–8 分鐘，完成後到首頁即可看到新集數。
        </p>

        <Field label="主題（必填）" required>
          <input
            type="text"
            value={topic}
            onChange={e => setTopic(e.target.value)}
            placeholder="例：咖啡冷知識、量子糾纏、Python GIL"
            className="w-full px-3 py-2 text-sm rounded-md border border-border bg-bg-primary text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </Field>

        <div className="grid grid-cols-2 gap-3">
          <Field label="切入角度">
            <Select value={angle} onChange={v => setAngle(v as typeof angle)} options={ANGLE_OPTIONS} />
          </Field>
          <Field label="主題類型">
            <Select value={topicType} onChange={v => setTopicType(v as typeof topicType)} options={TOPIC_TYPE_OPTIONS} />
          </Field>
          <Field label="長度 tier">
            <Select value={lengthTier} onChange={v => setLengthTier(v as typeof lengthTier)} options={LENGTH_TIER_OPTIONS} />
          </Field>
          <Field label="CEFR 難度">
            <Select value={cefr} onChange={v => setCefr(v as typeof cefr)} options={CEFR_OPTIONS} />
          </Field>
        </div>

        <Field label="收件使用者 IDs（可選）" hint="逗號或空白分隔；留空 = 純公開，不發個人通知">
          <input
            type="text"
            value={userIdsRaw}
            onChange={e => setUserIdsRaw(e.target.value)}
            placeholder="例：user-a, user-b"
            className="w-full px-3 py-2 text-sm rounded-md border border-border bg-bg-primary text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          />
        </Field>

        <Field label="交付日期">
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5 text-sm text-text-secondary cursor-pointer">
              <input
                type="checkbox"
                checked={overrideDate}
                onChange={e => setOverrideDate(e.target.checked)}
                className="accent-accent"
              />
              自訂日期
            </label>
            <input
              type="date"
              value={deliverDate}
              onChange={e => setDeliverDate(e.target.value)}
              disabled={!overrideDate}
              className="flex-1 px-3 py-2 text-sm rounded-md border border-border bg-bg-primary text-text-primary disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
          </div>
        </Field>

        {error && <ErrorBanner message={error} variant="inline" />}

        {lastResult && (
          <div className="px-3 py-2 rounded-lg bg-success/10 border border-success/20 text-xs text-success space-y-1">
            <div>
              <span className="font-medium">已排入佇列</span>
            </div>
            <div>msgId = <span className="font-mono">{lastResult.msgId}</span></div>
            <div>idempotencyKey = <span className="font-mono break-all">{lastResult.idempotencyKey}</span></div>
          </div>
        )}

        <Button
          variant="primary"
          size="md"
          onClick={() => void handleSubmit()}
          disabled={busy || !topic.trim()}
          className="w-full"
        >
          <Send size={14} />
          {busy ? '送出中…' : '送出生成請求'}
        </Button>
      </Card>

      {token && <TokenUsagePanel />}
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const totalSec = Math.round(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return min > 0 ? `${min}分${sec}秒` : `${sec}秒`
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return iso.replace('T', ' ').replace('Z', '')
}

/** 生成耗時明細：起訖時間 + 分階段耗時（gen_metrics.stages）。 */
function TokenUsagePanel() {
  const [data, setData] = useState<AdminTokenUsageResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandedSlug, setExpandedSlug] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    const load = async () => {
      setBusy(true)
      setError(null)
      try {
        const result = await api.getAdminTokenUsage()
        setData(result)
      } catch (err) {
        const msg = err instanceof AppError ? err.message : err instanceof Error ? err.message : '未知錯誤'
        setError(msg)
      } finally {
        setBusy(false)
      }
    }
    void load()
  }, [reloadKey])

  return (
    <Card className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <SectionLabel>生成紀錄與 Token 用量</SectionLabel>
        <Button size="sm" variant="ghost" onClick={() => setReloadKey(k => k + 1)} disabled={busy}>
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
          重新整理
        </Button>
      </div>

      {error && <ErrorBanner message={error} variant="inline" />}

      {data && (
        <>
          <div className="grid grid-cols-3 gap-3">
            <StatCard label="集數" value={data.episodeCount} />
            <StatCard label="輸入 tokens" value={data.totalInputTokens.toLocaleString()} />
            <StatCard label="輸出 tokens" value={data.totalOutputTokens.toLocaleString()} />
          </div>

          <div className="space-y-1">
            {data.items.length === 0 && (
              <p className="text-xs text-text-secondary py-4 text-center">尚無集數資料</p>
            )}
            {data.items.map(item => {
              const expanded = expandedSlug === item.slug
              const wallMs =
                item.generationStartedAt && item.generationFinishedAt
                  ? new Date(item.generationFinishedAt).getTime() - new Date(item.generationStartedAt).getTime()
                  : null
              return (
                <div key={item.slug} className="border border-border rounded-lg overflow-hidden">
                  <button
                    type="button"
                    onClick={() => setExpandedSlug(expanded ? null : item.slug)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-bg-secondary"
                    disabled={item.stages.length === 0}
                  >
                    {item.stages.length > 0 ? (
                      expanded ? <ChevronDown size={14} className="shrink-0 text-text-secondary" /> : <ChevronRight size={14} className="shrink-0 text-text-secondary" />
                    ) : (
                      <span className="w-3.5 shrink-0" />
                    )}
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-text-primary truncate">{item.title}</p>
                      <p className="text-[11px] text-text-secondary">
                        {formatDateTime(item.createdAt)}
                        {wallMs !== null && <span className="ml-2">耗時 {formatDuration(wallMs)}</span>}
                      </p>
                    </div>
                    <div className="text-[11px] text-text-secondary text-right shrink-0">
                      <div>入 {item.inputTokens.toLocaleString()}</div>
                      <div>出 {item.outputTokens.toLocaleString()}</div>
                    </div>
                  </button>

                  {expanded && item.stages.length > 0 && (
                    <div className="border-t border-border px-3 py-2 space-y-1 bg-bg-secondary/50">
                      {item.stages.map((stage, i) => (
                        <div key={`${stage.node}-${stage.attempt}-${i}`} className="flex items-center justify-between text-[11px]">
                          <span className={stage.status === 'failed' ? 'text-danger' : 'text-text-primary'}>
                            {stage.node}
                            {stage.attempt > 1 && <span className="text-text-secondary"> (第 {stage.attempt} 次)</span>}
                          </span>
                          <span className="text-text-secondary font-mono">{formatDuration(stage.durationMs)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </Card>
  )
}

interface FieldProps {
  readonly label: string
  readonly hint?: string
  readonly required?: boolean
  readonly children: React.ReactNode
}

function Field({ label, hint, required, children }: FieldProps) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-text-secondary">
        {label}
        {required && <span className="text-danger ml-1">*</span>}
      </span>
      {children}
      {hint && <span className="text-[11px] text-text-secondary">{hint}</span>}
    </label>
  )
}

interface SelectProps<T extends string> {
  readonly value: T
  readonly onChange: (v: T) => void
  readonly options: ReadonlyArray<{ readonly value: T; readonly label: string }>
}

function Select<T extends string>({ value, onChange, options }: SelectProps<T>) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value as T)}
      className="w-full px-3 py-2 text-sm rounded-md border border-border bg-bg-primary text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
    >
      {options.map(opt => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  )
}
