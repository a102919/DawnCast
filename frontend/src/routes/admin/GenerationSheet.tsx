// 單集生成過程 dialog（bottom sheet）：點列開啟時才抓 /admin/episodes/{id}/generation。
//
// 為什麼是 sheet 不是 inline 展開：生成紀錄有五段資料（階段耗時／LLM 呼叫明細／
// TTS 供應商／研究摘要／錯誤），inline 展開會把列表撐爆，也沒辦法給時間軸留寬度。
// 資料刻意不塞進 list 端點——llm_calls 一集可能數十筆，100 列 payload 會被撐爆。

import { useEffect, useState } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { api, AppError } from '../../api'
import type { AdminEpisodeGeneration, AdminEpisodeStats, StageMetric } from '../../api'
import { ErrorBanner, SectionLabel, Sheet } from '../../components/primitives'
import { formatDuration, PIPELINE_NODE_LABELS } from './pipelineLabels'

function errorMessage(err: unknown): string {
  if (err instanceof AppError) return err.message
  return err instanceof Error ? err.message : '未知錯誤'
}

// TTS provider 技術值 → 顯示文案。edge 是 MiniMax 失敗時的免費備援，特別標出來
// 讓人一眼看出「這集沒有語調參數」（edge-tts 不支援 emotion）。
const TTS_PROVIDER_LABELS: Readonly<Record<string, string>> = {
  minimax: 'MiniMax',
  edge: 'edge-tts（備援）',
}

const JUDGE_SCORE_LABELS: Readonly<Record<string, string>> = {
  hook_strength: '開場鉤子',
  informativeness: '資訊量',
  pacing: '節奏',
  chemistry: '雙人默契',
  groundedness: '事實查核',
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('zh-TW', { hour12: false })
}

/** 小型徽章：狀態／供應商等離散值。tone 對應全域色票。 */
function Badge({
  tone,
  children,
}: {
  readonly tone: 'success' | 'warning' | 'danger' | 'neutral'
  readonly children: React.ReactNode
}) {
  const toneClass = {
    success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning',
    danger: 'bg-danger/10 text-danger',
    neutral: 'bg-bg-secondary text-text-secondary',
  }[tone]
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${toneClass}`}>
      {children}
    </span>
  )
}

/** 摘要格：一格一個數字，維持表格密度（StatCard 對 sheet 內容來說太大）。 */
function MiniStat({
  label,
  value,
  subline,
}: {
  readonly label: string
  readonly value: string
  readonly subline?: string
}) {
  return (
    <div className="rounded-lg bg-bg-secondary/70 px-3 py-2">
      <div className="text-[11px] text-text-tertiary">{label}</div>
      <div className="text-sm font-semibold text-text-primary tabular-nums">{value}</div>
      {/* [opt-p2] subline 給附屬資訊用（例:cache 命中率）,用更小的字級 + 較弱
          顏色排入同格,維持 4 欄視覺節奏,不為輔助資訊加新欄。 */}
      {subline && (
        <div className="text-[10px] text-text-tertiary tabular-nums mt-0.5">{subline}</div>
      )}
    </div>
  )
}

function KvRow({ label, value }: { readonly label: string; readonly value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 text-xs">
      <span className="text-text-secondary shrink-0">{label}</span>
      <span className="text-text-primary text-right tabular-nums">{value}</span>
    </div>
  )
}

/** 階段時間軸：長條寬度與該階段耗時成正比，掃一眼就知道時間花在哪個節點。 */
function StageTimeline({ stages }: { readonly stages: readonly StageMetric[] }) {
  const maxMs = Math.max(...stages.map(s => s.durationMs), 1)
  return (
    <div className="space-y-1.5">
      {stages.map((stage, i) => (
        <div key={`${stage.node}-${stage.attempt}-${i}`} className="grid grid-cols-[7rem_1fr_4.5rem] items-center gap-2 text-[11px]">
          <span className={`truncate ${stage.status === 'failed' ? 'text-danger' : 'text-text-primary'}`}>
            {PIPELINE_NODE_LABELS[stage.node] ?? stage.node}
            {stage.attempt > 1 && <span className="text-text-secondary"> (第 {stage.attempt} 次)</span>}
          </span>
          <div className="h-2 rounded-full bg-bg-secondary overflow-hidden">
            <div
              className={`h-full rounded-full ${stage.status === 'failed' ? 'bg-danger/70' : 'bg-accent/60'}`}
              style={{ width: `${Math.max((stage.durationMs / maxMs) * 100, 1.5)}%` }}
            />
          </div>
          <span className="text-text-secondary font-mono text-right">{formatDuration(stage.durationMs)}</span>
        </div>
      ))}
    </div>
  )
}

function ResearchSection({ research }: { readonly research: AdminEpisodeGeneration['research'] }) {
  const hasAny =
    research.questionsCount != null ||
    research.sourceCount != null ||
    research.verifiedClaimCount != null ||
    research.judgeVerdict != null
  if (!hasAny) return <p className="text-xs text-text-tertiary">這集沒有研究紀錄（舊集數或未走研究流程）。</p>

  return (
    <div className="space-y-2">
      {research.grounded != null && (
        <KvRow
          label="事實依據"
          value={
            research.grounded ? <Badge tone="success">有來源佐證</Badge> : <Badge tone="warning">無來源（自由發揮）</Badge>
          }
        />
      )}
      {research.questionsCount != null && <KvRow label="研究子題" value={`${research.questionsCount} 題`} />}
      {research.sourceCount != null && <KvRow label="蒐集來源" value={`${research.sourceCount} 筆`} />}
      {Object.keys(research.providerCounts).length > 0 && (
        <KvRow
          label="來源分佈"
          value={Object.entries(research.providerCounts)
            .map(([name, count]) => `${name} ×${count}`)
            .join('、')}
        />
      )}
      {research.evidenceCardCount != null && <KvRow label="證據卡片" value={`${research.evidenceCardCount} 張`} />}
      {research.verifiedClaimCount != null && (
        <KvRow
          label="主張驗證"
          value={`${research.usableClaimCount ?? 0} 可用 / ${research.verifiedClaimCount} 筆（衝突 ${research.conflictCount ?? 0}）`}
        />
      )}
      {research.claimCheckTotal != null && research.claimCheckTotal > 0 && (
        <KvRow
          label="成稿查核"
          value={`${research.claimCheckSupported ?? 0} 有據 / ${research.claimCheckTotal} 筆`}
        />
      )}
      {Object.keys(research.judgeScores).length > 0 && (
        <KvRow
          label="品質評分"
          value={Object.entries(research.judgeScores)
            .map(([key, score]) => `${JUDGE_SCORE_LABELS[key] ?? key} ${score.toFixed(1)}`)
            .join('、')}
        />
      )}
      {research.judgeVerdict != null && (
        <KvRow
          label="評審結果"
          value={
            research.judgeVerdict === 'pass' ? (
              <Badge tone="success">通過</Badge>
            ) : (
              <Badge tone="warning">
                重寫{research.rewriteIterations != null ? ` ×${research.rewriteIterations}` : ''}
              </Badge>
            )
          }
        />
      )}
      {research.subtopics.length > 0 && (
        <div className="text-xs space-y-1">
          <span className="text-text-secondary">子題清單</span>
          <ul className="list-disc pl-4 space-y-0.5 text-text-primary">
            {research.subtopics.map(topic => (
              <li key={topic}>{topic}</li>
            ))}
          </ul>
        </div>
      )}
      {research.errors.length > 0 && (
        <div className="text-xs space-y-1">
          <span className="text-danger flex items-center gap-1">
            <AlertTriangle size={12} />
            研究過程警告
          </span>
          <ul className="list-disc pl-4 space-y-0.5 text-text-secondary">
            {research.errors.map(err => (
              <li key={err}>{err}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

interface GenerationSheetProps {
  /** null = 關閉。開啟時以 item.id 抓生成明細。 */
  readonly item: AdminEpisodeStats | null
  readonly onClose: () => void
}

export function GenerationSheet({ item, onClose }: GenerationSheetProps) {
  // state 都帶 id：換集數時舊資料因 id 不符自動視為未載入，effect 內不需同步
  // setState 清空（react-hooks/set-state-in-effect 禁止）。
  const [loaded, setLoaded] = useState<{ id: string; detail: AdminEpisodeGeneration } | null>(null)
  const [failure, setFailure] = useState<{ id: string; message: string } | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const episodeId = item?.id ?? null

  useEffect(() => {
    if (episodeId === null) return
    let cancelled = false
    const load = async () => {
      try {
        const result = await api.getAdminEpisodeGeneration(episodeId)
        if (!cancelled) {
          setLoaded({ id: episodeId, detail: result })
          setFailure(null)
        }
      } catch (err) {
        if (!cancelled) setFailure({ id: episodeId, message: errorMessage(err) })
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [episodeId, reloadKey])

  const detail = loaded !== null && loaded.id === episodeId ? loaded.detail : null
  const error = failure !== null && failure.id === episodeId ? failure.message : null

  const ttsProvider = detail?.tts?.provider ?? null

  return (
    <Sheet
      isOpen={item !== null}
      onClose={onClose}
      variant="bottom"
      ariaLabelledBy="generation-sheet-title"
      maxHeight="90vh"
      aboveBottomNav={false}
    >
      {/* min-h-0：flex 子元素預設不縮，少了它內容捲不動、捲動手勢會整張 sheet 被拖走。 */}
      <div className="min-h-0 overflow-y-auto px-4 pb-[calc(env(safe-area-inset-bottom,0px)+1.5rem)] pt-2 space-y-5">
        {item && (
          <header className="space-y-1.5">
            <h2 id="generation-sheet-title" className="text-base font-semibold text-text-primary">
              {item.title}
            </h2>
            <p className="text-[11px] text-text-secondary">
              {item.channelName ?? '無頻道'}
              <span className="mx-1.5">·</span>
              EP {item.episodeNo}
              <span className="mx-1.5">·</span>
              {item.publishedAt || '未發布'}
            </p>
            {detail && (
              <div className="flex flex-wrap gap-1.5">
                {detail.status === 'succeeded' ? (
                  <Badge tone="success">生成成功</Badge>
                ) : detail.status === 'failed' ? (
                  <Badge tone="danger">生成失敗</Badge>
                ) : detail.status !== '' ? (
                  <Badge tone="neutral">{detail.status}</Badge>
                ) : null}
                {ttsProvider !== null && (
                  <Badge tone={ttsProvider === 'minimax' ? 'success' : 'warning'}>
                    語音：{TTS_PROVIDER_LABELS[ttsProvider] ?? ttsProvider}
                  </Badge>
                )}
                {detail.research.engineUsed === 'failover' && <Badge tone="warning">改用備援引擎</Badge>}
              </div>
            )}
          </header>
        )}

        {error !== null && (
          <ErrorBanner message={error} variant="inline" onRetry={() => setReloadKey(k => k + 1)} />
        )}
        {error === null && detail === null && (
          <div className="flex items-center gap-2 py-8 justify-center text-text-secondary text-xs">
            <RefreshCw size={14} className="animate-spin" />
            載入生成紀錄中
          </div>
        )}

        {detail && (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              <MiniStat label="總耗時" value={detail.wallMs != null ? formatDuration(detail.wallMs) : '—'} />
              <MiniStat label="佇列等待" value={detail.queueWaitMs != null ? formatDuration(detail.queueWaitMs) : '—'} />
              <MiniStat
                label={`LLM tokens（${detail.totals.llmCallCount} 次呼叫）`}
                value={`入 ${detail.totals.inputTokens.toLocaleString()} / 出 ${detail.totals.outputTokens.toLocaleString()}`}
                subline={
                  detail.totals.cacheReadTokens > 0
                    ? `cache 命中 ${detail.totals.cacheReadTokens.toLocaleString()}`
                    : detail.totals.cacheCreationTokens > 0
                      ? `cache 寫入 ${detail.totals.cacheCreationTokens.toLocaleString()}（首次）`
                      : undefined
                }
              />
              <MiniStat label="語音合成字元" value={detail.tts != null ? detail.tts.characters.toLocaleString() : '—'} />
            </div>

            <div className="space-y-1">
              <KvRow label="開始時間" value={formatTime(detail.startedAt)} />
              <KvRow label="完成時間" value={formatTime(detail.finishedAt)} />
            </div>

            {detail.error != null && (
              <section className="space-y-1.5">
                <SectionLabel>失敗原因</SectionLabel>
                <div className="rounded-lg bg-danger/10 px-3 py-2 text-xs space-y-0.5">
                  <p className="text-danger font-medium">
                    {PIPELINE_NODE_LABELS[detail.error.node] ?? detail.error.node}
                    <span className="mx-1.5">·</span>
                    {detail.error.type}
                  </p>
                  <p className="text-text-secondary break-words">{detail.error.message}</p>
                </div>
              </section>
            )}

            <section className="space-y-1.5">
              <SectionLabel>生成階段</SectionLabel>
              {detail.stages.length > 0 ? (
                <StageTimeline stages={detail.stages} />
              ) : (
                <p className="text-xs text-text-tertiary">這集沒有階段紀錄（舊集數）。</p>
              )}
            </section>

            <section className="space-y-1.5">
              <SectionLabel>研究過程</SectionLabel>
              <ResearchSection research={detail.research} />
            </section>

            {detail.llmCalls.length > 0 && (
              <section className="space-y-1.5">
                <SectionLabel>LLM 呼叫明細</SectionLabel>
                <div className="space-y-1">
                  {detail.llmCalls.map((call, i) => (
                    <div
                      key={`${call.node}-${call.call}-${i}`}
                      className="grid grid-cols-[1fr_auto_auto] items-baseline gap-3 text-[11px]"
                    >
                      <span className="truncate text-text-primary">
                        {PIPELINE_NODE_LABELS[call.node] ?? call.node}
                        {call.call !== '' && <span className="text-text-tertiary font-mono"> {call.call}</span>}
                        {call.segmentIndex != null && <span className="text-text-tertiary"> 段 {call.segmentIndex + 1}</span>}
                        {call.attempt > 1 && <span className="text-text-secondary"> (第 {call.attempt} 次)</span>}
                      </span>
                      <span className="text-text-secondary tabular-nums">
                        入 {call.inputTokens.toLocaleString()} / 出 {call.outputTokens.toLocaleString()}
                      </span>
                      <span className="text-text-secondary font-mono text-right w-14">{formatDuration(call.durationMs)}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </Sheet>
  )
}
