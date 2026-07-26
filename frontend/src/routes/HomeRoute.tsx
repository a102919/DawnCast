import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Play, Brain, SearchX } from 'lucide-react'
import { Chip } from '../components/primitives/Chip'
import { SectionLabel } from '../components/primitives/SectionLabel'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { TodayHeroCard } from '../components/home/TodayHeroCard'
import { HomeHeroFallback } from '../components/home/HomeHeroFallback'
import { WeeklyCard } from '../components/home/WeeklyCard'
import { useVocab } from '../state'
import { EpisodeRow } from '../components/shared/EpisodeRow'
import { api } from '../api'
import { TOPIC_LABELS } from '../lib'
import type { TopicKey, MockEpisode } from '../lib'
import type { Episode } from '../types/episode'

/** 今日推薦最多顯示幾張（featured 不夠時用 published desc 補到這個上限）。 */
const TODAY_PICKS_LIMIT = 2

// 集數庫進退場：tween + 自訂 ease，半透明 + 微縮放。empty 狀態與每張卡片共用。
const EPISODE_CARD_MOTION = {
  initial: { opacity: 0, scale: 0.96 },
  animate: {
    opacity: 1,
    scale: 1,
    transition: { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] as const },
  },
  exit: {
    opacity: 0,
    scale: 0.96,
    transition: { duration: 0.18, ease: [0.2, 0.8, 0.2, 1] as const },
  },
} as const

export function HomeRoute() {
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])
  // 每集時長（秒），從 cues 末段推算；單集 fetch 失敗時留空 → 卡片顯示「—」
  const [durations, setDurations] = useState<ReadonlyMap<string, number>>(new Map())
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [topicFilter, setTopicFilter] = useState<TopicKey>('all')
  // 今日已送達集數的 Episode 內容（id 對齊 listEpisodes）；null = 沒送達，fallback
  const [deliveredEpisode, setDeliveredEpisode] = useState<Episode | null>(null)
  const { items: vocabItems } = useVocab()
  const today = new Date().toISOString().slice(0, 10)
  const dueCount = vocabItems.filter(v => !v.nextReview || v.nextReview <= today).length

  // 給「繼續學習」按鈕帶位用：新到首集，沒有就 fallback 留空（按鈕仍渲染但網址無效）
  const continueTargetId = episodes[0]?.id ?? null

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      setFetchError(null)
      try {
        // 並行：列舉集數 + 今日送達查詢，分享同一份錯誤處理
        const [list, delivered] = await Promise.all([
          api.listEpisodes(),
          api.getDeliveredEpisode(today).catch(() => null),
        ])
        if (cancelled) return
        setEpisodes(list)
        setDeliveredEpisode(delivered)
        // 一次抓所有集數的 cues 推算時長；單集失敗不影響整體
        const results = await Promise.all(
          list.map(ep => api.getEpisode(ep.id).catch(() => null)),
        )
        if (cancelled) return
        const durMap = new Map<string, number>()
        for (const full of results) {
          if (!full) continue
          const lastCue = full.cues.at(-1)
          if (lastCue) durMap.set(full.id, lastCue.end)
        }
        setDurations(durMap)
      } catch {
        if (!cancelled) setFetchError('節目資料載入失敗，請重試')
      }
    }
    void load()

    return () => {
      cancelled = true
    }
  }, [retryKey, today])

  // 今日 hero 對應的 MockEpisode（借 listEpisodes 的封面/主題/徽章資訊）
  const todayEpisode = useMemo(() => {
    if (!deliveredEpisode) return null
    return episodes.find(ep => ep.id === deliveredEpisode.id) ?? null
  }, [deliveredEpisode, episodes])

  // Fallback 用的「精選」集：優先 is_featured，其次全集第一集
  const fallbackFeatured = useMemo(() => {
    return episodes.find(ep => ep.isFeatured) ?? episodes[0] ?? null
  }, [episodes])

  // 今日推薦網格用：排除今日 Hero 已顯示的那集，其餘 featured 先 + published desc 補到上限
  const todayPicks = useMemo(() => {
    const deliveryId = todayEpisode?.id
    const featured = episodes.filter(ep => ep.isFeatured && ep.id !== deliveryId)
    const others = episodes
      .filter(ep => !ep.isFeatured && ep.id !== deliveryId)
      .sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1))
    return [...featured, ...others].slice(0, TODAY_PICKS_LIMIT)
  }, [episodes, todayEpisode])

  const filteredEpisodes = topicFilter === 'all'
    ? episodes
    : episodes.filter(ep => ep.topic === topicFilter)

  return (
    <div className="max-w-3xl mx-auto px-4 pt-5 pb-4 space-y-6">

      {/* ── 今日推薦（Hero + 2 張小卡並排同高；全部都沒才整個不渲染）── */}
      {(todayEpisode || fallbackFeatured || todayPicks.length > 0) && (
        <section className="space-y-3" data-testid="weekly-row">
          <SectionLabel>今日推薦</SectionLabel>
          <div className="flex flex-col sm:flex-row items-stretch gap-3">
            {(todayEpisode || fallbackFeatured) && (
              <div className="min-w-0 sm:flex-1">
                {todayEpisode && (
                  <TodayHeroCard
                    ep={todayEpisode}
                    duration={durations.get(todayEpisode.id)}
                    today={today}
                  />
                )}
                {!todayEpisode && fallbackFeatured && (
                  <HomeHeroFallback featured={fallbackFeatured} />
                )}
              </div>
            )}
            {todayPicks.length > 0 && (
              <div
                className="grid grid-cols-2 gap-3 w-full sm:flex sm:flex-col sm:w-[38%] sm:shrink-0"
                data-testid="weekly-scroll"
              >
                {todayPicks.map(ep => (
                  <WeeklyCard key={ep.id} ep={ep} duration={durations.get(ep.id)} className="sm:flex-1" />
                ))}
              </div>
            )}
          </div>
        </section>
      )}

      {/* ── 學習入口 ── */}
      <div className="grid grid-cols-2 gap-3">
        <Link to={`/player/${continueTargetId ?? ''}`}>
          <button
            type="button"
            className="w-full h-14 rounded-lg bg-accent text-white font-medium flex items-center justify-center gap-2 hover:bg-accent-hover active:scale-[0.98] transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
          >
            <Play size={16} fill="currentColor" />
            <span>繼續學習</span>
          </button>
        </Link>
        <Link to="/flashcards" className="relative">
          <button
            type="button"
            className="w-full h-14 rounded-lg bg-bg-secondary border border-border text-text-primary font-medium flex items-center justify-center gap-2 hover:bg-border active:scale-[0.98] transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
          >
            <Brain size={16} />
            <span>閃卡複習</span>
          </button>
          {dueCount > 0 && (
            <span
              aria-label={`待複習 ${dueCount} 張`}
              className="absolute -top-1.5 -right-1.5 min-w-[20px] h-5 px-1.5 rounded-full bg-accent text-white text-[10px] font-semibold flex items-center justify-center ring-2 ring-bg-primary"
            >
              {dueCount}
            </span>
          )}
        </Link>
      </div>

      {/* ── 集數庫 ── */}
      <section className="space-y-4">
        <SectionLabel>選擇 podcast 開始學習</SectionLabel>
        <div className="flex gap-1.5 flex-wrap">
          {(Object.keys(TOPIC_LABELS) as TopicKey[]).map(key => (
            <Chip
              key={key}
              active={topicFilter === key}
              onClick={() => setTopicFilter(key)}
            >
              {TOPIC_LABELS[key]}
            </Chip>
          ))}
        </div>
        {fetchError !== null && (
          <ErrorBanner variant="inline" message={fetchError} onRetry={() => setRetryKey(k => k + 1)} />
        )}
        <AnimatePresence mode="popLayout" initial={false}>
          {filteredEpisodes.length === 0 ? (
            <motion.div
              key="empty"
              {...EPISODE_CARD_MOTION}
              className="flex flex-col items-center justify-center gap-2 py-10 text-text-tertiary"
            >
              <SearchX size={28} />
              <p className="text-sm">此主題目前尚無集數</p>
            </motion.div>
          ) : (
            <motion.div key="grid" layout className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <AnimatePresence mode="popLayout" initial={false}>
                {filteredEpisodes.map(ep => (
                  <motion.div key={ep.id} layout {...EPISODE_CARD_MOTION}>
                    <EpisodeRow ep={ep} variant="card" duration={durations.get(ep.id)} />
                  </motion.div>
                ))}
              </AnimatePresence>
            </motion.div>
          )}
        </AnimatePresence>
      </section>

    </div>
  )
}
