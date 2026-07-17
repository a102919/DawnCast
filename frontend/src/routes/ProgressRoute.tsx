import { useEffect, useState, type ComponentType } from 'react'
import { Headphones, BookOpen, Flame, Clock, Search } from 'lucide-react'
import { useVocab, useListened, useActivity } from '../state'
import { api } from '../api'
import type { MockEpisode } from './episodeData'

function calcStreak(dates: readonly string[]): number {
  if (dates.length === 0) return 0
  const unique = [...new Set(dates)].sort().reverse()
  const today = new Date().toLocaleDateString('en-CA')
  const yesterday = new Date(Date.now() - 86400000).toLocaleDateString('en-CA')
  if (unique[0] !== today && unique[0] !== yesterday) return 0
  let streak = 1
  for (let i = 1; i < unique.length; i++) {
    const prev = new Date(unique[i - 1]!)
    const curr = new Date(unique[i]!)
    const diffDays = Math.round((prev.getTime() - curr.getTime()) / 86400000)
    if (diffDays === 1) streak++
    else break
  }
  return streak
}

export function ProgressRoute() {
  const { items } = useVocab()
  const { listenedIds } = useListened()
  const { streakDates, listenMinutes, lookupCount } = useActivity()
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])

  useEffect(() => {
    api.listEpisodes()
      .then(setEpisodes)
      .catch(() => {
        // 統計摘要頁：載入失敗靜默處理，不擋整頁
      })
  }, [])

  const listenedEps = episodes.filter(ep => listenedIds.has(ep.id))
  const yearMonth = new Date().toLocaleDateString('en-CA').slice(0, 7)

  const streak = calcStreak(streakDates)
  const thisMonthMinutes = listenMinutes[yearMonth] ?? 0
  const thisMonthLookups = lookupCount[yearMonth] ?? 0
  const thisMonthVocab = items.filter(v => v.createdAt.startsWith(yearMonth)).length

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-6">
      <h1 className="text-xl font-semibold text-text-primary">學習進度</h1>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <StatCard Icon={Headphones} label="已聽集數" value={listenedIds.size} unit="集" />
        <StatCard Icon={BookOpen} label="累積詞彙" value={items.length} unit="個" />
        <StatCard Icon={Flame} label="連續天數" value={streak} unit="天" />
        <StatCard Icon={Clock} label="本月聆聽" value={thisMonthMinutes} unit="分" />
        <StatCard Icon={Search} label="本月查詞" value={thisMonthLookups} unit="次" />
        <StatCard Icon={BookOpen} label="本月新增" value={thisMonthVocab} unit="個" />
      </div>

      {listenedEps.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-text-tertiary uppercase tracking-wider">已完成的集數</h2>
          <div className="space-y-2">
            {listenedEps.map(ep => (
              <div key={ep.id} className="flex items-center gap-3 p-3 rounded-lg bg-bg-secondary border border-border">
                <div className="w-8 h-8 rounded-full bg-success/10 flex items-center justify-center text-success shrink-0">
                  <Headphones size={14} />
                </div>
                <div className="min-w-0">
                  <div className="text-sm font-medium text-text-primary">{ep.title}</div>
                  <div className="text-xs text-text-secondary">{ep.titleZh}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {listenedEps.length === 0 && (
        <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
          <div className="w-12 h-12 rounded-full bg-bg-secondary flex items-center justify-center text-text-tertiary">
            <Headphones size={22} />
          </div>
          <p className="text-text-secondary text-sm">聽完 80% 的集數後會自動記錄</p>
        </div>
      )}
    </div>
  )
}

function StatCard({
  Icon,
  label,
  value,
  unit,
}: {
  readonly Icon: ComponentType<{ size: number }>
  readonly label: string
  readonly value: number | string
  readonly unit: string
}) {
  return (
    <div className="p-4 rounded-lg border border-border bg-bg-secondary space-y-1 text-center">
      <div className="flex justify-center text-accent mb-1">
        <Icon size={18} />
      </div>
      <div className="text-2xl font-bold text-text-primary">
        {value}
        {unit && <span className="text-sm font-normal text-text-secondary ml-0.5">{unit}</span>}
      </div>
      <div className="text-xs text-text-tertiary">{label}</div>
    </div>
  )
}
