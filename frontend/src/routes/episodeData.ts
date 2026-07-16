// ─── Types ───────────────────────────────────────────────────────────────────

export type TopicKey = 'all' | 'tech' | 'business' | 'culture' | 'science'

export const TOPIC_LABELS: Record<TopicKey, string> = {
  all: '全部',
  tech: '科技',
  business: '商業',
  culture: '文化',
  science: '科學',
} as const

export type CefrLevel = 'A2' | 'B1' | 'B2'

export type MockEpisode = {
  readonly id: string
  readonly title: string
  readonly titleZh: string
  readonly topic: Exclude<TopicKey, 'all'>
  readonly cefrLevel: CefrLevel
  readonly isFeatured?: boolean
  readonly episode: number
  readonly publishedAt: string
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

export function formatDateZhTW(isoDate: string): string {
  // ponytail: 後端尚未回傳真實 publishedAt，給空字串；new Date('') 是 Invalid Date，
  // 直接 format 會 RangeError 把整個 HomeRoute 炸白。fallback 回原文（空字串也 OK）。
  const d = new Date(isoDate)
  if (Number.isNaN(d.getTime())) return isoDate
  return new Intl.DateTimeFormat('zh-TW', { month: 'long', day: 'numeric' }).format(d)
}

// ─── Static data ─────────────────────────────────────────────────────────────

export const EPISODES: readonly MockEpisode[] = [
  {
    id: 'loop_engineering',
    title: 'Loop Engineering',
    titleZh: '迴圈工程',
    topic: 'tech',
    cefrLevel: 'B1',
    episode: 1,
    publishedAt: '2026-06-16',
  },
  {
    id: 'startup_culture',
    title: 'Startup Culture',
    titleZh: '新創文化',
    topic: 'business',
    cefrLevel: 'B1',
    isFeatured: true,
    episode: 2,
    publishedAt: '2026-06-09',
  },
  {
    id: 'climate_change',
    title: 'Climate Change',
    titleZh: '氣候變遷',
    topic: 'science',
    cefrLevel: 'B1',
    isFeatured: true,
    episode: 3,
    publishedAt: '2026-06-02',
  },
  {
    id: 'remote_work',
    title: 'Remote Work',
    titleZh: '遠端工作',
    topic: 'culture',
    cefrLevel: 'B1',
    episode: 4,
    publishedAt: '2026-05-26',
  },
  {
    id: 'ai_revolution',
    title: 'AI Revolution',
    titleZh: 'AI 革命',
    topic: 'tech',
    cefrLevel: 'B1',
    episode: 5,
    publishedAt: '2026-05-19',
  },
  {
    id: 'coffee_culture',
    title: 'Coffee Culture',
    titleZh: '咖啡文化',
    topic: 'culture',
    cefrLevel: 'A2',
    episode: 6,
    publishedAt: '2026-05-12',
  },
] as const

export const CEFR_COLOR: Record<CefrLevel, string> = {
  A2: 'bg-cefr-a2-bg text-cefr-a2',
  B1: 'bg-cefr-b1-bg text-cefr-b1',
  B2: 'bg-cefr-b2-bg text-cefr-b2',
} as const

export function episodeTitleById(id: string): string {
  return EPISODES.find(ep => ep.id === id)?.titleZh ?? id
}