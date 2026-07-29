// 管理後台下拉選單的選項。值一律對齊後端 enum（shared/models/api.py 與 engine.py），
// 標籤是台灣正體中文。

import type { CefrLevel, ChannelCategory, LengthTier, TopicType } from '../../api'

export const TOPIC_TYPE_OPTIONS = [
  { value: 'evergreen', label: '常青（不受時效影響）' },
  { value: 'news', label: '時效新聞' },
  { value: 'product', label: '產品介紹' },
  { value: 'skill', label: '技能教學' },
] as const satisfies ReadonlyArray<{ value: TopicType; label: string }>

export const LENGTH_TIER_OPTIONS = [
  { value: 'short', label: '短（約 3 分鐘）' },
  { value: 'medium', label: '中（約 5–8 分鐘）' },
  { value: 'long', label: '長（約 10–12 分鐘）' },
] as const satisfies ReadonlyArray<{ value: LengthTier; label: string }>

export const CEFR_OPTIONS = [
  { value: 'A2', label: 'A2 初級' },
  { value: 'B1', label: 'B1 中級' },
  { value: 'B2', label: 'B2 中高級' },
] as const satisfies ReadonlyArray<{ value: CefrLevel; label: string }>

export const CHANNEL_CATEGORY_OPTIONS = [
  { value: 'tech', label: '科技' },
  { value: 'business', label: '商業' },
  { value: 'culture', label: '文化' },
  { value: 'science', label: '科學' },
] as const satisfies ReadonlyArray<{ value: ChannelCategory; label: string }>
