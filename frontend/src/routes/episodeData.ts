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

export const CEFR_COLOR: Record<CefrLevel, string> = {
  A2: 'bg-cefr-a2-bg text-cefr-a2',
  B1: 'bg-cefr-b1-bg text-cefr-b1',
  B2: 'bg-cefr-b2-bg text-cefr-b2',
} as const

// ─── 測試 fixture 專用（保留 seed 假資料給 *.test.tsx 用） ─────────────────
//
// Runtime 不再 export EPISODES 假資料——所有畫面從 `api.listEpisodes()` 拿 DB 真資料。
// 測試若需要固定資料再從這裡 import；production code 不該碰到這份。
export const SEED_EPISODES_FOR_TEST: readonly MockEpisode[] = [
  {
    id: 'episode_test_seed_1',
    title: 'Test Seed Episode',
    titleZh: '測試用 seed 集數',
    topic: 'tech',
    cefrLevel: 'B1',
    episode: 1,
    publishedAt: '2026-07-01',
  },
] as const

export function episodeTitleById(id: string): string {
  return id
}
