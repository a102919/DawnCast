// Episode domain 的共用型別與常數（原 routes/episodeData.ts，搬到 lib 消除
// api/ 與 components/ 反向依賴頁面層的問題）。

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

export const CEFR_COLOR: Record<CefrLevel, string> = {
  A2: 'bg-cefr-a2-bg text-cefr-a2',
  B1: 'bg-cefr-b1-bg text-cefr-b1',
  B2: 'bg-cefr-b2-bg text-cefr-b2',
} as const
