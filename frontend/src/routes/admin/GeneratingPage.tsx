// 生成中頁面：目前正在跑的集數列表，每 5 秒輪詢一次。
//
// 只能看到「跑了多久」，看不到跑到哪個階段——worker 是同步跑完整條 LangGraph
// 才落地一次（見 episode_pipeline_runs 表），中途沒有寫入任何 stage 進度。

import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { toast } from 'sonner'
import { Loader2, Mic, X } from 'lucide-react'
import { api, AppError } from '../../api'
import type { AdminRunningJob } from '../../api'
import { Card, EmptyState, ErrorBanner, SectionLabel } from '../../components/primitives'
import { formatDuration } from './pipelineLabels'

const POLL_MS = 5000

function errorMessage(err: unknown): string {
  if (err instanceof AppError) return err.message
  return err instanceof Error ? err.message : '未知錯誤'
}

// idempotencyKey 是 `{deliverDate}:{bigTopic}:{angle}:{lengthTier}:{topicType}`
// 或（cluster_id 情境）`{clusterId}:{lengthTier}:{topicType}`——後兩段固定是
// lengthTier/topicType，往前抓到日期前綴才拆得出主題文字，抓不到就整串當標題。
function describeRun(idempotencyKey: string): { primary: string; secondary: string } {
  const parts = idempotencyKey.split(':')
  if (parts.length >= 5 && /^\d{4}-\d{2}-\d{2}$/.test(parts[0])) {
    const topicType = parts[parts.length - 1]
    const lengthTier = parts[parts.length - 2]
    const angle = parts[parts.length - 3]
    const topic = parts.slice(1, parts.length - 3).join(':')
    return { primary: topic, secondary: `${angle} · ${lengthTier} · ${topicType}` }
  }
  return { primary: idempotencyKey, secondary: '' }
}

export function GeneratingPage() {
  const [jobs, setJobs] = useState<readonly AdminRunningJob[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const result = await api.listAdminRunningJobs()
        if (!cancelled) {
          setJobs(result)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(errorMessage(err))
      }
    }
    void load()
    const timer = setInterval(() => void load(), POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <SectionLabel>生成中</SectionLabel>
        <span className="text-[11px] text-text-secondary">每 5 秒自動更新</span>
      </div>

      {error && <ErrorBanner message={error} variant="inline" />}

      <Card className="p-4 space-y-2">
        {jobs?.length === 0 && !error && (
          <EmptyState icon={Mic} size="compact" title="目前沒有正在生成的集數" />
        )}

        <div className="space-y-2">
          {jobs?.map(job => (
            <JobRow key={job.runId} job={job} onCancelled={() => setJobs(prev => prev?.filter(j => j.runId !== job.runId) ?? null)} />
          ))}
        </div>
      </Card>
    </div>
  )
}

function JobRow({ job, onCancelled }: { readonly job: AdminRunningJob; readonly onCancelled: () => void }) {
  const [confirming, setConfirming] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const { primary, secondary } = describeRun(job.idempotencyKey)

  const handleCancel = async () => {
    setCancelling(true)
    try {
      await api.cancelAdminRunningJob(job.runId)
      toast.success('已移除這筆生成紀錄')
      onCancelled()
    } catch (err) {
      toast.error(errorMessage(err))
      setCancelling(false)
    }
  }

  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2.5">
        <Loader2 size={14} className="shrink-0 text-accent animate-spin" />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-text-primary truncate">{primary}</p>
          {secondary && <p className="text-[11px] text-text-secondary truncate">{secondary}</p>}
        </div>
        <span className="text-[11px] text-text-secondary tabular-nums shrink-0">
          {job.elapsedSec != null ? formatDuration(job.elapsedSec * 1000) : '—'}
          {job.attempt > 1 && <span className="ml-1.5">· 第 {job.attempt} 次嘗試</span>}
        </span>
        <button
          type="button"
          onClick={() => setConfirming(true)}
          disabled={confirming || cancelling}
          aria-label={`取消「${primary}」`}
          className="shrink-0 p-1.5 -mr-1 rounded text-text-secondary transition-[color,transform] duration-fast ease-apple hover:text-danger active:scale-[0.9] disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <X size={14} />
        </button>
      </div>

      <AnimatePresence>
        {confirming && (
          <motion.div
            key="confirm-cancel-job"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="flex items-center gap-3 px-3 py-2.5 border-t border-border bg-bg-secondary">
              <span className="text-xs text-danger mr-auto">只會移除追蹤紀錄，不會中止背景生成</span>
              <button
                type="button"
                onClick={() => setConfirming(false)}
                disabled={cancelling}
                className="text-sm text-text-secondary px-3 py-1.5 rounded-lg border border-border bg-bg-primary hover:bg-bg-secondary transition-colors min-h-[36px] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                返回
              </button>
              <button
                type="button"
                onClick={() => void handleCancel()}
                disabled={cancelling}
                className="text-sm text-white font-medium px-3 py-1.5 rounded-lg bg-danger hover:opacity-90 transition-opacity min-h-[36px] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {cancelling ? '處理中…' : '確定移除'}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
