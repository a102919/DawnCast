import { useMemo } from 'react'
import { Atom, Briefcase, Cpu, Palette, RadioTower, type LucideIcon } from 'lucide-react'
import { getCoverArt, coverArtBackground, COVER_GRAIN_URL } from '../../lib'

type ChannelCoverSize = 'sm' | 'md' | 'lg' | 'xl'

interface ChannelCoverProps {
  readonly url?: string | null
  readonly size: ChannelCoverSize
  /** 生成藝術的種子與色票依據：沒有真實封面圖時，用 slug+topic 決定 deterministic
   *  漸層與圖示（同 EpisodeCover 的 getCoverArt 邏輯）。 */
  readonly slug: string
  readonly topic?: string
  readonly className?: string
}

const SIZE_CLASS: Record<ChannelCoverSize, string> = {
  sm: 'w-10 h-10 rounded-lg shadow-sm',
  md: 'w-16 h-16 rounded-xl shadow',
  lg: 'w-24 h-24 rounded-2xl shadow-md',
  xl: 'w-32 h-32 rounded-2xl shadow-md',
}

const ICON_SIZE: Record<ChannelCoverSize, number> = {
  sm: 16,
  md: 26,
  lg: 40,
  xl: 52,
}

/** 主題圖示語彙對齊 EpisodeCover，讓同主題的頻道封面與集數封面用同一套視覺語言。 */
const TOPIC_ICON: Record<string, LucideIcon> = {
  tech: Cpu,
  business: Briefcase,
  culture: Palette,
  science: Atom,
}

/**
 * 頻道封面：有 `url` 顯示簽章後的真實圖片；沒有則用 slug/topic 生成 Apple Music
 * 質感的抽象漸層封面（同 EpisodeCover 的 getCoverArt/coverArtBackground 生成邏輯），
 * 取代單調的灰底圖示。admin 管理列與使用者端頻道卡片/詳情頁共用同一份元件——
 * 長得一樣的東西必須用同一份程式碼。
 */
export function ChannelCover({ url, size, slug, topic, className = '' }: ChannelCoverProps) {
  const art = useMemo(() => getCoverArt(slug, topic), [slug, topic])

  if (url) {
    return (
      // alt 留空：頻道名就在旁邊，重複朗讀反而干擾（裝飾性圖片）。
      <img src={url} alt="" className={`${SIZE_CLASS[size]} object-cover shrink-0 ${className}`} />
    )
  }

  const Icon = (topic && TOPIC_ICON[topic]) || RadioTower

  return (
    <div
      className={`relative overflow-hidden shrink-0 border border-white/15 ${SIZE_CLASS[size]} ${className}`}
    >
      {/* 1. Fluid Mesh 漸層底圖（同 EpisodeCover） */}
      <div className="absolute inset-0" style={{ background: coverArtBackground(art) }} />

      {/* 2. 頂部玻璃反光鏡面 */}
      <div className="absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/25 via-white/5 to-transparent pointer-events-none" />

      {/* 3. 菲林微顆粒感 */}
      <div
        className="absolute inset-0 opacity-30 mix-blend-overlay pointer-events-none"
        style={{ backgroundImage: `url("${COVER_GRAIN_URL}")` }}
      />

      {/* 4. 置中主題圖示 */}
      <div className="absolute inset-0 grid place-items-center text-white/90 drop-shadow-sm pointer-events-none">
        <Icon size={ICON_SIZE[size]} strokeWidth={1.75} />
      </div>
    </div>
  )
}
