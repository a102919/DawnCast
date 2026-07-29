import type { Api } from './types'
import { mockApi } from './mockApi'
import { httpApi } from './httpApi'

// 預設走 mock（不破壞既有 demo）。只有明確 VITE_USE_MOCK='false' 才接真實後端。
export const api: Api = import.meta.env.VITE_USE_MOCK === 'false' ? httpApi : mockApi

export { AppError } from './httpApi'
export type {
  AccountInfo,
  AdminEpisodeStats,
  AdminEpisodeStatsResponse,
  Api,
  CefrLevel,
  Channel,
  ChannelCategory,
  ChannelPlanResponse,
  ChannelPublic,
  ChannelStatus,
  ChannelTopic,
  CreateChannelInput,
  DailyOrder,
  DailyOrderInput,
  DailyOrderStatus,
  DictEntry,
  EntryMode,
  LengthTier,
  RecommendedEpisode,
  Settings,
  StageMetric,
  TopicType,
  UpdateChannelInput,
  VocabItem,
} from './types'
