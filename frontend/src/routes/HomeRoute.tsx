import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Play, SearchX, CalendarDays } from 'lucide-react'
import { Chip } from '../components/primitives/Chip'
import { SectionLabel } from '../components/primitives/SectionLabel'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { TodayHeroCard } from '../components/home/TodayHeroCard'
import { HomeHeroFallback } from '../components/home/HomeHeroFallback'
import { WeeklyCard } from '../components/home/WeeklyCard'
import { ChannelsRail } from '../components/home/ChannelsRail'
import { RecommendedRail } from '../components/home/RecommendedRail'
import { useDailyOrder, useEpisodes } from '../state'
import { EpisodeRow } from '../components/shared/EpisodeRow'
import { api } from '../api'
import { TOPIC_LABELS } from '../lib'
import type { TopicKey } from '../lib'
import type { Episode } from '../types/episode'

/** 今日推薦最多顯示幾張（featured 不夠時用 published desc 補到這個上限）。 */
const TODAY_PICKS_LIMIT = 2

// episode-readiness 輪詢：worker 跑完就停。間隔遞增避免對剛排隊的 job 過度打，
// 上限 16s 對齊「生成中」視覺節奏（不會讓 spinner 看起來卡住）。
// ponytail: 30 次無命中後停止。worker 卡死時不會無限打；離開頁面再回來會重新輪詢。
const POLL_DELAYS_MS = [2000, 4000, 8000, 16000, 16000, 16000, 16000, 16000, 16000, 16000,
  16000, 16000, 16000, 16000, 16000, 16000, 16000, 16000, 16000, 16000,
  16000, 16000, 16000, 16000, 16000, 16000, 16000, 16000, 16000, 16000] as const
const MAX_POLL_ATTEMPTS = POLL_DELAYS_MS.length

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
  const { episodes, error, refresh } = useEpisodes()
  // 每集時長（秒），從 cues 末段推算；單集 fetch 失敗時留空 → 卡片顯示「—」
  const [durations, setDurations] = useState<ReadonlyMap<string, number>>(new Map())
  const [topicFilter, setTopicFilter] = useState<TopicKey>('all')
  // 進行中訂單已送達的集數（id 對齊 listEpisodes）；null = 還沒送達，fallback
  const [deliveredEpisode, setDeliveredEpisode] = useState<Episode | null>(null)
  // 交付 deliveredEpisode 的訂單 id，跟著上面一起設，供 hero 卡片組 player 連結；
  // 不直接讀當下的 activeOrderId——生成完成當下 activeOrder 就會翻 null（見
  // migration 0025：ready 不再算進行中），但 hero 卡片這一輪還在顯示已送達的
  // 那集，連結仍要指回原本那張訂單。
  const [deliveredOrderId, setDeliveredOrderId] = useState<string | null>(null)
  const { activeOrder, refresh: refreshOrders } = useDailyOrder()
  const activeOrderId = activeOrder?.id ?? null

  // 給「繼續學習」按鈕帶位用：新到首集，沒有就 fallback 留空（按鈕仍渲染但網址無效）
  const continueTargetId = episodes[0]?.id ?? null

  // episodes 清單本身由 EpisodesProvider 集中抓取（見 useEpisodes）；這裡只負責
  // 進行中訂單的送達查詢 + 逐集補抓 cues 推算時長。沒有進行中訂單就沒有 hero，
  // 直接清空（例如上一筆訂單生成完成後 activeOrder 翻 null）。
  useEffect(() => {
    let cancelled = false

    const load = async () => {
      const delivered = activeOrderId
        ? await api.getDeliveredEpisode(activeOrderId).catch(() => null)
        : null
      if (cancelled) return
      setDeliveredEpisode(delivered)
      setDeliveredOrderId(delivered ? activeOrderId : null)
      // 逐集補抓 cues 推算時長；單集失敗不影響整體
      const results = await Promise.all(
        episodes.map(ep => api.getEpisode(ep.id).catch(() => null)),
      )
      if (cancelled) return
      const durMap = new Map<string, number>()
      for (const full of results) {
        if (!full) continue
        const lastCue = full.cues.at(-1)
        if (lastCue) durMap.set(full.id, lastCue.end)
      }
      setDurations(durMap)
    }
    void load()

    return () => {
      cancelled = true
    }
  }, [episodes, activeOrderId])

  // 今日 hero 對應的 MockEpisode（借 listEpisodes 的封面/主題/徽章資訊）
  const todayEpisode = useMemo(() => {
    if (!deliveredEpisode) return null
    return episodes.find(ep => ep.id === deliveredEpisode.id) ?? null
  }, [deliveredEpisode, episodes])

  // Episode readiness polling：送出點餐後 worker 開始跑，產出後 user 沒 reload
  // 永遠看不到「可收聽」。條件：有進行中訂單 + deliveredEpisode 還沒拿到 →
  // 每 2s→16s 輪詢，命中就 refresh orders（讓 activeOrder 翻 null）並停止。
  // 進度由 timerRef.current 控制，cancelled 旗標防 setState-after-unmount。
  const shouldPoll = activeOrderId !== null && deliveredEpisode === null
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelledRef = useRef<boolean>(false)
  useEffect(() => {
    cancelledRef.current = false
    if (!shouldPoll || !activeOrderId) return
    let attempt = 0
    const tick = async () => {
      if (cancelledRef.current) return
      if (attempt >= MAX_POLL_ATTEMPTS) return
      const delay = POLL_DELAYS_MS[attempt]
      attempt += 1
      timerRef.current = setTimeout(async () => {
        if (cancelledRef.current) return
        try {
          const d = await api.getDeliveredEpisode(activeOrderId)
          if (cancelledRef.current) return
          if (d) {
            setDeliveredEpisode(d)
            setDeliveredOrderId(activeOrderId)
            // 命中：連帶刷全集數庫（hero card 需要封面/主題）+ 訂單狀態。
            void refresh()
            void refreshOrders()
            return
          }
        } catch {
          // 輪詢失敗不 throw；下次 tick 再試
        }
        void tick()
      }, delay)
    }
    void tick()
    return () => {
      cancelledRef.current = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [shouldPoll, activeOrderId, refresh, refreshOrders])

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
                {todayEpisode && deliveredOrderId && (
                  <TodayHeroCard
                    ep={todayEpisode}
                    duration={durations.get(todayEpisode.id)}
                    orderId={deliveredOrderId}
                  />
                )}
                {!todayEpisode && fallbackFeatured && (
                  <HomeHeroFallback featured={fallbackFeatured} />
                )}
              </div>
            )}
            {todayPicks.length > 0 && (
              <div
                className="grid grid-cols-2 gap-3 w-full sm:flex sm:flex-col sm:w-[40%] sm:shrink-0 justify-between"
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
        <Link to="/daily" aria-label="立即點播">
          <button
            type="button"
            className="w-full h-14 rounded-lg bg-bg-secondary border border-border text-text-primary font-medium flex items-center justify-center gap-2 hover:bg-border active:scale-[0.98] transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1"
          >
            <CalendarDays size={16} />
            <span>立即點播</span>
          </button>
        </Link>
      </div>

      {/* ── 你追蹤的頻道 + 根據追蹤頻道的推薦 ── */}
      <ChannelsRail />
      <RecommendedRail />

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
        {error !== null && (
          <ErrorBanner variant="inline" message={error} onRetry={() => void refresh()} />
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
