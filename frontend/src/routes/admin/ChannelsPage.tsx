// 頻道管理面板：建立頻道、調整定位、觸發選題、檢視／否決選題庫、上傳封面。
//
// 這是「有內容才產」機制的操作介面：01:00 選題把候選寫進 backlog，02:00 只挑
// 有合格候選的頻道出刊。所以這個面板最重要的數字是每個頻道的「候選」數——它是零，
// 那個頻道明天就不會出刊，這是設計，不是故障。

import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'
import {
  ChevronDown, ChevronRight, Image as ImageIcon, Pause, Play,
  Plus, RadioTower, RefreshCw, Sparkles, X,
} from 'lucide-react'
import { api, AppError } from '../../api'
import type { Channel, ChannelCategory, ChannelStatus, ChannelTopic, CefrLevel, LengthTier, TopicType } from '../../api'
import { Button, Card, EmptyState, ErrorBanner, SectionLabel } from '../../components/primitives'
import { ChannelCover } from '../../components/shared/ChannelCover'
import { Field, Select, inputClass } from './fields'
import { CEFR_OPTIONS, CHANNEL_CATEGORY_OPTIONS, LENGTH_TIER_OPTIONS, TOPIC_TYPE_OPTIONS } from './options'

/** 對齊後端 channel_cover_max_bytes 與 content-type allowlist（shared/config.py、
 *  admin.py 的 magic bytes 檢查）。前端擋是體驗，真正的把關在後端。 */
const MAX_COVER_BYTES = 2 * 1024 * 1024
const ACCEPTED_COVER = 'image/jpeg,image/png,image/webp'

/** 對齊後端 channel_min_topic_score 預設值：低於這個分數的候選不會被排進生產。 */
const MIN_TOPIC_SCORE = 0.6

const STATUS_LABELS: Record<string, string> = {
  active: '啟用中',
  paused: '已暫停',
  archived: '已封存',
}

const TOPIC_STATUS_LABELS: Record<string, string> = {
  candidate: '候選',
  scheduled: '已排程',
  published: '已出刊',
  rejected: '已否決',
  stale: '已過期',
}

function errorMessage(err: unknown): string {
  if (err instanceof AppError) return err.message
  return err instanceof Error ? err.message : '未知錯誤'
}

export function ChannelsPage() {
  const [channels, setChannels] = useState<readonly Channel[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  // 沿用同頁 TokenUsagePanel 的作法：重新整理＝bump 一個 key，載入邏輯留在 effect 裡。
  const [reloadKey, setReloadKey] = useState(0)
  const reload = useCallback(() => setReloadKey(k => k + 1), [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setBusy(true)
      setError(null)
      try {
        const result = await api.listAdminChannels()
        if (!cancelled) setChannels(result)
      } catch (err) {
        if (!cancelled) setError(errorMessage(err))
      } finally {
        if (!cancelled) setBusy(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  return (
    <Card className="p-4 space-y-4">
      <div className="flex items-center justify-between">
        <SectionLabel>頻道</SectionLabel>
        <Button size="sm" variant="ghost" onClick={reload} disabled={busy}>
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
          重新整理
        </Button>
      </div>
      <p className="text-xs text-text-secondary leading-relaxed">
        每天 01:00 依頻道定位補充選題庫，02:00 只挑「有合格候選」的頻道出刊。
        候選數是 0 的頻道明天就不會出刊——這是「有內容才產」的設計。
      </p>

      {error && <ErrorBanner message={error} variant="inline" />}

      {channels.length === 0 && !busy && !error && (
        <EmptyState
          icon={RadioTower}
          size="compact"
          title="還沒有頻道"
          description="建立第一個頻道，系統就會開始依它的定位持續選題。"
        />
      )}

      <div className="space-y-2">
        {channels.map(channel => (
          <ChannelRow key={channel.id} channel={channel} onChanged={reload} />
        ))}
      </div>

      {creating ? (
        <NewChannelForm
          onCancel={() => setCreating(false)}
          onCreated={() => {
            setCreating(false)
            reload()
          }}
        />
      ) : (
        <Button size="md" variant="secondary" className="w-full" onClick={() => setCreating(true)}>
          <Plus size={14} />
          新增頻道
        </Button>
      )}
    </Card>
  )
}

interface ChannelRowProps {
  readonly channel: Channel
  readonly onChanged: () => void
}

function ChannelRow({ channel, onChanged }: ChannelRowProps) {
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(false)

  // 動作全部共用一個 busy 旗標與錯誤處理：這一列同時只會有一個動作在跑。
  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      toast.success(label)
      onChanged()
    } catch (err) {
      toast.error(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const handleCover = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // 先清空再處理：同一個檔案連選兩次也要能觸發 change（瀏覽器比對 value 決定是否發事件）。
    e.target.value = ''
    if (!file) return
    if (file.size > MAX_COVER_BYTES) {
      toast.error('封面超過 2 MB，請先壓縮')
      return
    }
    await run('封面已更新', () => api.uploadAdminChannelCover(channel.id, file))
  }

  const paused = channel.status !== 'active'

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors duration-fast ease-apple hover:bg-bg-secondary"
      >
        {expanded ? (
          <ChevronDown size={14} className="shrink-0 text-text-secondary" />
        ) : (
          <ChevronRight size={14} className="shrink-0 text-text-secondary" />
        )}
        <ChannelCover url={channel.coverImageUrl} slug={channel.slug} topic={channel.topic} size="sm" />
        <div className="flex-1 min-w-0">
          <p className="text-sm text-text-primary truncate flex items-center gap-1.5">
            {channel.name}
            <StatusBadge status={channel.status} />
          </p>
          <p className="text-[11px] text-text-secondary truncate">
            <span className="font-mono">{channel.slug}</span>
            <span className="mx-1.5">·</span>
            每 {channel.targetIntervalDays} 天
          </p>
        </div>
        <div className="text-[11px] text-text-secondary text-right shrink-0 tabular-nums">
          <div>EP {channel.episodeCount}</div>
          <div className={channel.candidateCount === 0 ? 'text-warning' : ''}>
            候選 {channel.candidateCount}
          </div>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-border px-3 py-3 space-y-3 bg-bg-secondary/50">
          <p className="text-xs text-text-secondary leading-relaxed whitespace-pre-wrap">
            {channel.themePrompt}
          </p>

          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() =>
                void run('已排入選題佇列，worker 跑完候選才會出現', () => api.planAdminChannel(channel.id))
              }
            >
              <Sparkles size={14} />
              觸發選題
            </Button>

            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() =>
                void run(paused ? '已啟用' : '已暫停', () =>
                  api.updateAdminChannel(channel.id, { status: paused ? 'active' : 'paused' }),
                )
              }
            >
              {paused ? <Play size={14} /> : <Pause size={14} />}
              {paused ? '啟用' : '暫停'}
            </Button>

            {/* label 包住隱藏 input 就是原生的檔案選取，不另外做拖放區。
                ponytail: 上傳中只給狀態文字不給進度條——fetch 沒有上傳進度，
                要真進度得換成 XHR，2 MB 上限下不值得。 */}
            <label
              className={`inline-flex items-center gap-1.5 font-medium px-3 py-1.5 text-xs rounded-sm min-h-[44px] border border-border bg-bg-secondary text-text-primary transition-[background-color,transform] duration-fast ease-apple active:scale-[0.97] ${
                busy ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:bg-border'
              }`}
            >
              <ImageIcon size={14} />
              {busy ? '處理中…' : '換封面'}
              <input
                type="file"
                accept={ACCEPTED_COVER}
                disabled={busy}
                onChange={e => void handleCover(e)}
                className="sr-only"
              />
            </label>
          </div>

          <TopicBacklog channelId={channel.id} onChanged={onChanged} />
        </div>
      )}
    </div>
  )
}


function StatusBadge({ status }: { readonly status: string }) {
  const active = status === 'active'
  return (
    <span
      className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium ${
        active ? 'bg-success/10 text-success' : 'bg-bg-secondary text-text-secondary border border-border'
      }`}
    >
      {STATUS_LABELS[status] ?? status}
    </span>
  )
}

interface TopicBacklogProps {
  readonly channelId: string
  readonly onChanged: () => void
}

/** 選題庫：展開頻道時才載入（沒展開的頻道不打 API）。 */
function TopicBacklog({ channelId, onChanged }: TopicBacklogProps) {
  const [topics, setTopics] = useState<readonly ChannelTopic[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setError(null)
      try {
        const result = await api.listAdminChannelTopics(channelId)
        if (!cancelled) setTopics(result)
      } catch (err) {
        if (!cancelled) setError(errorMessage(err))
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [channelId, reloadKey])

  const reject = async (topic: ChannelTopic) => {
    try {
      await api.updateAdminChannelTopic(channelId, topic.id, { status: 'rejected' })
      toast.success('已否決，不會被排進生產')
      setReloadKey(k => k + 1)
      // 頻道那列的候選數也要跟著減一。
      onChanged()
    } catch (err) {
      toast.error(errorMessage(err))
    }
  }

  if (error) return <ErrorBanner message={error} variant="inline" />
  if (topics === null) return <p className="text-[11px] text-text-secondary py-2">載入選題庫…</p>

  if (topics.length === 0) {
    return (
      <p className="text-[11px] text-text-secondary py-3 text-center">
        這個頻道的選題庫是空的，按上面「觸發選題」補充。
      </p>
    )
  }

  return (
    <div className="space-y-1">
      <p className="text-[11px] font-medium text-text-secondary">選題庫（{topics.length}）</p>
      {topics.map(topic => (
        <div
          key={topic.id}
          className="flex items-start gap-2 px-2 py-1.5 rounded border border-border bg-bg-primary"
        >
          <span
            className={`text-[11px] font-mono tabular-nums shrink-0 mt-0.5 ${
              topic.score >= MIN_TOPIC_SCORE ? 'text-text-primary' : 'text-text-secondary'
            }`}
            title={topic.score >= MIN_TOPIC_SCORE ? undefined : `低於門檻 ${MIN_TOPIC_SCORE}，不會被排進生產`}
          >
            {topic.score.toFixed(2)}
          </span>
          <div className="flex-1 min-w-0">
            <p className="text-xs text-text-primary break-words">{topic.canonicalTopic}</p>
            <p className="text-[11px] text-text-secondary">
              {topic.angle}
              <span className="mx-1.5">·</span>
              {TOPIC_STATUS_LABELS[topic.status] ?? topic.status}
            </p>
          </div>
          {topic.status === 'candidate' && (
            <button
              type="button"
              onClick={() => void reject(topic)}
              aria-label={`否決「${topic.canonicalTopic}」`}
              className="shrink-0 p-2 -m-1 rounded text-text-secondary transition-[color,transform] duration-fast ease-apple hover:text-danger active:scale-[0.9] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              <X size={14} />
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

interface NewChannelFormProps {
  readonly onCreated: () => void
  readonly onCancel: () => void
}

function NewChannelForm({ onCreated, onCancel }: NewChannelFormProps) {
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [themePrompt, setThemePrompt] = useState('')
  const [description, setDescription] = useState('')
  const [topic, setTopic] = useState<ChannelCategory>('tech')
  const [topicType, setTopicType] = useState<TopicType>('evergreen')
  const [lengthTier, setLengthTier] = useState<LengthTier>('medium')
  const [cefrLevel, setCefrLevel] = useState<CefrLevel>('B1')
  const [interval, setInterval] = useState(3)
  const [status] = useState<ChannelStatus>('active')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    // 邊界驗證：slug 進 URL 與 unique index，格式錯了後端會炸成 500，先在這裡擋。
    if (!name.trim() || !themePrompt.trim()) {
      setError('名稱與頻道定位不可為空')
      return
    }
    if (!/^[a-z0-9-]+$/.test(slug.trim())) {
      setError('代稱只能用小寫英文、數字與連字號')
      return
    }
    if (interval < 1 || interval > 30) {
      setError('出刊間隔請填 1–30 天')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.createAdminChannel({
        slug: slug.trim(),
        name: name.trim(),
        themePrompt: themePrompt.trim(),
        topic,
        description: description.trim() || null,
        topicType,
        lengthTier,
        cefrLevel,
        targetIntervalDays: interval,
        status,
      })
      toast.success('頻道已建立')
      onCreated()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="border border-border rounded-lg p-3 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-text-secondary">新增頻道</p>
        <Button size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          取消
        </Button>
      </div>

      <Field label="名稱（必填）" required>
        <input
          type="text"
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="例：AI 工作現場"
          className={inputClass}
        />
      </Field>

      <Field label="代稱（必填）" hint="網址用，只能小寫英文、數字與連字號">
        <input
          type="text"
          value={slug}
          onChange={e => setSlug(e.target.value)}
          placeholder="例：ai-at-work"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          className={`${inputClass} font-mono`}
        />
      </Field>

      <Field
        label="頻道定位（必填）"
        required
        hint="給選題 LLM 的指示：這個頻道要持續產出什麼樣的內容、避開什麼"
      >
        <textarea
          value={themePrompt}
          onChange={e => setThemePrompt(e.target.value)}
          rows={3}
          placeholder="例：聚焦 AI agent 在真實工作場景的應用與限制，偏好有具體案例、可查證的內容，避免純預測與行銷語言"
          className={`${inputClass} resize-y`}
        />
      </Field>

      <Field label="簡介（可選）" hint="給使用者看的一句話說明">
        <input
          type="text"
          value={description}
          onChange={e => setDescription(e.target.value)}
          placeholder="例：每週追一次 AI 在辦公室裡真正做到了什麼"
          className={inputClass}
        />
      </Field>

      <div className="grid grid-cols-2 gap-3">
        <Field label="分類">
          <Select value={topic} onChange={setTopic} options={CHANNEL_CATEGORY_OPTIONS} />
        </Field>
        <Field label="主題類型" hint="時效／產品類會先抓搜尋素材再選題">
          <Select value={topicType} onChange={setTopicType} options={TOPIC_TYPE_OPTIONS} />
        </Field>
        <Field label="長度 tier">
          <Select value={lengthTier} onChange={setLengthTier} options={LENGTH_TIER_OPTIONS} />
        </Field>
        <Field label="CEFR 難度">
          <Select value={cefrLevel} onChange={setCefrLevel} options={CEFR_OPTIONS} />
        </Field>
      </div>

      <Field label="出刊間隔（天）" hint="同時是硬下限與飢餓權重分母：拖越久沒出刊，排序越優先">
        <input
          type="number"
          min={1}
          max={30}
          value={interval}
          onChange={e => setInterval(Number(e.target.value))}
          className={inputClass}
        />
      </Field>

      {error && <ErrorBanner message={error} variant="inline" />}

      <Button
        variant="primary"
        size="md"
        className="w-full"
        disabled={busy || !name.trim() || !slug.trim() || !themePrompt.trim()}
        onClick={() => void handleSubmit()}
      >
        <Plus size={14} />
        {busy ? '建立中…' : '建立頻道'}
      </Button>
    </div>
  )
}
