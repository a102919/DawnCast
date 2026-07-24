import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Play, Brain, SearchX } from 'lucide-react'
import { Chip } from '../components/primitives/Chip'
import { SectionLabel } from '../components/primitives/SectionLabel'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { useVocab } from '../state'
import { EpisodeRow } from '../components/shared/EpisodeRow'
import { api } from '../api'
import { TOPIC_LABELS } from '../lib'
import type { TopicKey, MockEpisode } from '../lib'

export function HomeRoute() {
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])
  // 每集時長（秒），從 cues 末段推算；單集 fetch 失敗時留空 → 卡片顯示「—」
  const [durations, setDurations] = useState<ReadonlyMap<string, number>>(new Map())
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [topicFilter, setTopicFilter] = useState<TopicKey>('all')
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
        const list = await api.listEpisodes()
        if (cancelled) return
        setEpisodes(list)
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
  }, [retryKey])

  const filteredEpisodes = topicFilter === 'all'
    ? episodes
    : episodes.filter(ep => ep.topic === topicFilter)

  return (
    <div className="max-w-3xl mx-auto px-4 pt-5 pb-4 space-y-6">

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
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1, transition: { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] } }}
              exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.18, ease: [0.2, 0.8, 0.2, 1] } }}
              className="flex flex-col items-center justify-center gap-2 py-10 text-text-tertiary"
            >
              <SearchX size={28} />
              <p className="text-sm">此主題目前尚無集數</p>
            </motion.div>
          ) : (
            <motion.div key="grid" layout className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <AnimatePresence mode="popLayout" initial={false}>
                {filteredEpisodes.map(ep => (
                  <motion.div
                    key={ep.id}
                    layout
                    initial={{ opacity: 0, scale: 0.96 }}
                    animate={{ opacity: 1, scale: 1, transition: { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] } }}
                    exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.18, ease: [0.2, 0.8, 0.2, 1] } }}
                  >
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
