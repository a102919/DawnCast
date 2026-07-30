// 單集數據頁：播放次數 / 聽完人數 / 收藏數 / token 用量 / 生成耗時總覽。
//
// playCount 是累積計數器，只從 migration 0023 部署後起算——沒有歷史，這是
// 設計不是漏資料。listenerCount 是跨 user_activity 即時統計，有全部歷史。
// 兩個數字不會一致，是預期行為。

import { useEffect, useState } from 'react'
import { BarChart3, ChevronRight, Cpu, Headphones, Heart, Play, RefreshCw } from 'lucide-react'
import { api, AppError } from '../../api'
import type { AdminEpisodeStats, AdminEpisodeStatsResponse } from '../../api'
import { Button, Card, Chip, EmptyState, ErrorBanner, SectionLabel, StatCard } from '../../components/primitives'
import { GenerationSheet } from './GenerationSheet'
import { formatDuration } from './pipelineLabels'

function errorMessage(err: unknown): string {
  if (err instanceof AppError) return err.message
  return err instanceof Error ? err.message : '未知錯誤'
}

type SortKey = 'recent' | 'plays' | 'listeners' | 'cost'

const SORT_OPTIONS: ReadonlyArray<{ key: SortKey; label: string }> = [
  { key: 'recent', label: '最新' },
  { key: 'plays', label: '播放最多' },
  { key: 'listeners', label: '聽完最多' },
  { key: 'cost', label: '最貴' },
]

// 後端已經是 createdAt desc；'recent' 不重排，其餘複製一份陣列再排序（不 mutate 原陣列）。
// 手機三欄卡片塞不下全長數字（如 1,096,043），用緊湊單位顯示量級即可
function formatCompact(n: number): string {
  return new Intl.NumberFormat('zh-TW', { notation: 'compact', maximumFractionDigits: 1 }).format(n)
}

function sortItems(items: readonly AdminEpisodeStats[], sort: SortKey): readonly AdminEpisodeStats[] {
  switch (sort) {
    case 'plays':
      return [...items].sort((a, b) => b.playCount - a.playCount)
    case 'listeners':
      return [...items].sort((a, b) => b.listenerCount - a.listenerCount)
    case 'cost':
      return [...items].sort((a, b) => b.inputTokens + b.outputTokens - (a.inputTokens + a.outputTokens))
    case 'recent':
      return items
  }
}

export function EpisodesPage() {
  // 整包回應存成一個 state：彙總數字與明細來自同一次查詢，拆成多個 state 只會多出
  // 「彼此可能不同步」的空間。
  const [stats, setStats] = useState<AdminEpisodeStatsResponse | null>(null)
  const [sort, setSort] = useState<SortKey>('recent')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  // 生成過程 dialog：存整列（sheet header 要標題/頻道資訊），null = 關閉。
  const [detailItem, setDetailItem] = useState<AdminEpisodeStats | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setBusy(true)
      setError(null)
      try {
        const result = await api.getAdminEpisodeStats()
        if (!cancelled) setStats(result)
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

  const sorted = sortItems(stats?.items ?? [], sort)
  const totalTokens = stats ? stats.totalInputTokens + stats.totalOutputTokens : 0

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <SectionLabel>單集數據</SectionLabel>
        <Button size="sm" variant="ghost" onClick={() => setReloadKey(k => k + 1)} disabled={busy}>
          <RefreshCw size={14} className={busy ? 'animate-spin' : ''} />
          重新整理
        </Button>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <StatCard icon={BarChart3} label="集數" value={stats?.episodeCount ?? 0} />
        <StatCard icon={Play} label="總播放" value={stats?.totalPlayCount ?? 0} />
        <StatCard icon={Cpu} label="總 tokens" value={formatCompact(totalTokens)} />
      </div>

      {error && <ErrorBanner message={error} variant="inline" />}

      <Card className="p-4 space-y-4">
        <div className="flex flex-wrap gap-2">
          {SORT_OPTIONS.map(opt => (
            <Chip key={opt.key} active={sort === opt.key} onClick={() => setSort(opt.key)}>
              {opt.label}
            </Chip>
          ))}
        </div>

        {sorted.length === 0 && !busy && !error && (
          <EmptyState icon={BarChart3} size="compact" title="還沒有任何集數" />
        )}

        <div className="space-y-2">
          {sorted.map(item => (
            <EpisodeStatsRow key={item.id} item={item} onOpen={() => setDetailItem(item)} />
          ))}
        </div>
      </Card>

      <GenerationSheet item={detailItem} onClose={() => setDetailItem(null)} />
    </div>
  )
}

function EpisodeStatsRow({ item, onOpen }: { readonly item: AdminEpisodeStats; readonly onOpen: () => void }) {
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={onOpen}
        className="w-full flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 text-left transition-colors duration-fast ease-apple hover:bg-bg-secondary"
      >
        <div className="flex items-center gap-2 min-w-0">
          <ChevronRight size={14} className="shrink-0 text-text-secondary" />
          <div className="min-w-0">
            <p className="text-sm text-text-primary truncate">{item.title}</p>
            <p className="text-[11px] text-text-secondary truncate">
              {item.channelName ?? '無頻道'}
              <span className="mx-1.5">·</span>
              EP {item.episodeNo}
              <span className="mx-1.5">·</span>
              {item.publishedAt || '未發布'}
              <span className="mx-1.5">·</span>
              {item.isFree ? '公開' : '私有'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-[11px] text-text-secondary ml-auto tabular-nums">
          <span className="flex items-center gap-1" title="播放次數（僅計 migration 0023 部署後）">
            <Play size={12} />
            {item.playCount}
          </span>
          <span className="flex items-center gap-1" title="聽完人數">
            <Headphones size={12} />
            {item.listenerCount}
          </span>
          <span className="flex items-center gap-1" title="收藏數">
            <Heart size={12} />
            {item.favoriteCount}
          </span>
          <span className="text-right">
            <div>入 {item.inputTokens.toLocaleString()}</div>
            <div>出 {item.outputTokens.toLocaleString()}</div>
          </span>
          <span className="w-14 text-right shrink-0">
            {item.wallMs !== null && item.wallMs !== undefined ? formatDuration(item.wallMs) : '—'}
          </span>
        </div>
      </button>
    </div>
  )
}
