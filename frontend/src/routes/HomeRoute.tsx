import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Play, Captions, MousePointerClick, BookOpen, Headphones, Brain, Star, SearchX } from 'lucide-react'
import type { Episode } from '../types/episode'
import { Button } from '../components/primitives/Button'
import { Chip } from '../components/primitives/Chip'
import { SectionLabel } from '../components/primitives/SectionLabel'
import { StatCard } from '../components/primitives/StatCard'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { useActivity, useVocab } from '../state'
import { EpisodeRow } from '../components/shared/EpisodeRow'
import { api } from '../api'
import { TOPIC_LABELS, CEFR_COLOR } from '../lib'
import type { TopicKey, MockEpisode } from '../lib'
import { storageGet } from '../lib/storage'

const FEATURES = [
  {
    Icon: Captions,
    title: '雙語字幕同步',
    desc: '英文與中文字幕逐句高亮，跟著語速吸收語感',
  },
  {
    Icon: MousePointerClick,
    title: '點擊查單字',
    desc: '點任何字幕單字，立即查看音標、詞性、中文釋義',
  },
  {
    Icon: BookOpen,
    title: '個人單字本',
    desc: '收錄陌生單字，隨時翻閱複習，持續累積詞彙量',
  },
] as const

export function HomeRoute() {
  // 集數庫從 API 拿 DB 真資料；首次渲染空陣列 + skeleton（useEffect 跑完就有資料）。
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [topicFilter, setTopicFilter] = useState<TopicKey>('all')
  const { listenedEpisodeIds } = useActivity()
  const { items: vocabItems } = useVocab()
  const today = new Date().toISOString().slice(0, 10)
  const dueCount = vocabItems.filter(v => !v.nextReview || v.nextReview <= today).length

  const WEEKLY_COUNT = 2
  const weeklyEps = episodes.slice(0, WEEKLY_COUNT)
  const weeklyProgress = weeklyEps.filter(ep => listenedEpisodeIds.has(ep.id)).length

  useEffect(() => {
    const load = async () => {
      setFetchError(null)
      setEpisode(null)
      try {
        const list = await api.listEpisodes()
        setEpisodes(list)
        const featured = list[0]
        if (featured) {
          const data = await api.getEpisode(featured.id)
          setEpisode(data)
        }
      } catch {
        setFetchError('節目資料載入失敗，請重試')
      }
    }
    void load()
  }, [retryKey])

  const filteredEpisodes = topicFilter === 'all'
    ? episodes
    : episodes.filter(ep => ep.topic === topicFilter)

  const latestEpisode = episodes[0]

  // P0-1：繼續收聽入口——讀 LS 取得最後播放的集數 ID 與時間
  const lastPlayed = (() => {
    const episodeId = storageGet<string>('dawncast:player:lastEpisodeId')
    if (!episodeId) return null
    // LS 可能殘留「已壞掉」的舊 episode（mp3 不在了）→ 用 episodes 列表驗證還在線，
    // 不在線就 fallback 到最新一集，避免按鈕把使用者帶進 404。
    if (episodes.length > 0 && !episodes.some(ep => ep.id === episodeId)) return null
    const saved = storageGet<{ episodeId: string; currentTime: number }>('dawncast:player:currentTime')
    if (!saved || saved.episodeId !== episodeId || saved.currentTime <= 0) return null
    const mm = Math.floor(saved.currentTime / 60)
    const ss = Math.floor(saved.currentTime % 60)
    return { episodeId, formattedTime: `${mm}:${String(ss).padStart(2, '0')}` }
  })()

  return (
    <div className="max-w-3xl mx-auto px-4 pt-5 pb-4 space-y-8">

      {/* ── Hero ── */}
      <section className="text-center space-y-4 pt-2">
        <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-accent/10 text-accent text-xs font-medium">
          <Star size={12} fill="currentColor" />
          全功能免費開放
        </div>
        <h1 className="text-display lg:text-4xl tracking-display leading-display font-bold text-text-primary">
          繼續你的學習之旅
        </h1>
        <div className="flex flex-col sm:flex-row items-center justify-center gap-3 pt-2">
          <Link to={`/player${lastPlayed ? `/${lastPlayed.episodeId}` : latestEpisode ? `/${latestEpisode.id}` : ''}`}>
            <Button variant="primary" size="lg">
              <Play size={16} fill="currentColor" />
              繼續學習
            </Button>
          </Link>
          <Link to="/flashcards">
            <Button variant="secondary" size="lg">
              <Brain size={16} />
              閃卡複習{dueCount > 0 ? `（${dueCount}）` : ''}
            </Button>
          </Link>
        </div>
        {lastPlayed && (
          <div className="pt-3">
            <Link
              to={`/player/${lastPlayed.episodeId}`}
              className="inline-flex items-center gap-1.5 text-xs text-text-tertiary hover:text-accent transition-colors duration-fast"
            >
              <Play size={11} fill="currentColor" />
              繼續收聽最後播放（{lastPlayed.formattedTime}）
            </Link>
          </div>
        )}
      </section>

      {/* ── 今日推薦 ── */}
      {latestEpisode && (
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <SectionLabel>今日推薦</SectionLabel>
          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[latestEpisode.cefrLevel]}`}>
            {latestEpisode.cefrLevel}
          </span>
        </div>
        {fetchError !== null && (
          <ErrorBanner variant="inline" message={fetchError} onRetry={() => setRetryKey(k => k + 1)} />
        )}
        <EpisodeRow ep={latestEpisode} variant="hero" title={episode?.title ?? null} />
      </section>
      )}

      {/* ── 本週進度 ── */}
      <section className="space-y-2">
        <div className="flex items-center justify-between text-xs text-text-tertiary">
          <span className="font-medium">本週進度</span>
          <span className={weeklyProgress >= WEEKLY_COUNT ? 'text-success font-medium' : ''}>
            {weeklyProgress}/{WEEKLY_COUNT} 集
          </span>
        </div>
        <div className="h-2 bg-bg-secondary rounded-full overflow-hidden">
          <div
            className="h-full bg-accent rounded-full transition-all duration-700 ease-apple"
            style={{ width: `${(weeklyProgress / WEEKLY_COUNT) * 100}%` }}
          />
        </div>
        {weeklyProgress >= WEEKLY_COUNT && (
          <p className="text-xs text-success font-medium">本週完整學習達成！</p>
        )}
      </section>

      {/* ── 學習統計 ── */}
      <section className="grid grid-cols-3 gap-3">
        <StatCard icon={Headphones} label="已聽集數" value={listenedEpisodeIds.size} unit="集" />
        <StatCard icon={BookOpen} label="單字庫" value={vocabItems.length} unit="個" />
        <StatCard icon={Brain} label="今日待複習" value={dueCount} unit="張" />
      </section>

      {/* ── 集數庫 ── */}
      <section className="space-y-4">
        <SectionLabel>所有集數</SectionLabel>
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
                    <EpisodeRow ep={ep} variant="card" />
                  </motion.div>
                ))}
              </AnimatePresence>
            </motion.div>
          )}
        </AnimatePresence>
      </section>

      {/* ── 功能亮點 ── */}
      <section className="space-y-4">
        <SectionLabel>不只是聆聽，真正學進去</SectionLabel>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {FEATURES.map(({ Icon, title, desc }) => (
            <div key={title} className="flex items-start gap-3 sm:block sm:space-y-2 p-4 rounded-lg border border-border bg-bg-secondary">
              <div className="w-8 h-8 rounded-md bg-accent/10 flex items-center justify-center text-accent shrink-0">
                <Icon size={18} />
              </div>
              <div>
                <div className="font-medium text-text-primary text-sm">{title}</div>
                <div className="text-xs text-text-secondary leading-relaxed mt-0.5 sm:mt-0">{desc}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

    </div>
  )
}
