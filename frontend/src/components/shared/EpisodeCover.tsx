import { useMemo } from 'react'
import {
  Cpu,
  Bot,
  TrendingUp,
  Briefcase,
  Camera,
  Palette,
  Atom,
  Globe,
  Code,
  Headphones,
  FlaskConical,
  Music,
  Zap,
  ShieldCheck,
  HeartPulse,
  Rocket,
  Database,
  Cloud,
  Landmark,
  Lightbulb,
  Compass,
  Feather,
  Radio,
  type LucideIcon,
} from 'lucide-react'
import { getCoverArt, coverArtBackground, COVER_GRAIN_URL, type TopicKey } from '../../lib'

type CoverSize = 'sm' | 'md' | 'lg' | 'hero'

interface EpisodeCoverProps {
  readonly episodeId: string
  readonly size: CoverSize
  /** 主題類別（科技、商業、文化、科學） */
  readonly topic?: TopicKey
  /** AI 自動推薦的專屬圖示名稱（例如 'cpu', 'bot', 'trending-up', 'rocket' 等） */
  readonly coverIcon?: string | null
  /** 未來接真封面圖：有值時直接 render <img>，不跑生成邏輯。 */
  readonly imageUrl?: string | null
  readonly className?: string
}

const SIZE_CLASS: Record<CoverSize, string> = {
  sm: 'w-10 h-10 rounded-lg shadow-sm',
  md: 'w-16 h-16 rounded-xl shadow',
  lg: 'w-28 h-28 rounded-2xl shadow-md',
  hero: 'w-full aspect-square rounded-2xl sm:rounded-3xl shadow-lg',
}

/** key 一律為去除分隔符的小寫名稱，查表前用 normalizeIconName 對齊。 */
const ICON_BY_NAME: Record<string, LucideIcon> = {
  cpu: Cpu,
  bot: Bot,
  trendingup: TrendingUp,
  briefcase: Briefcase,
  camera: Camera,
  palette: Palette,
  atom: Atom,
  globe: Globe,
  code: Code,
  headphones: Headphones,
  flaskconical: FlaskConical,
  music: Music,
  zap: Zap,
  shieldcheck: ShieldCheck,
  heartpulse: HeartPulse,
  rocket: Rocket,
  database: Database,
  cloud: Cloud,
  landmark: Landmark,
  lightbulb: Lightbulb,
  compass: Compass,
  feather: Feather,
  radio: Radio,
}

const TOPIC_DEFAULT_ICON: Partial<Record<TopicKey, LucideIcon>> = {
  tech: Cpu,
  business: Briefcase,
  culture: Palette,
  science: Atom,
}

const WATERMARK_SIZE: Record<CoverSize, number> = {
  sm: 48,
  md: 48,
  lg: 84,
  hero: 128,
}

/** 'trending-up'、'trending_up'、'TrendingUp' 一律對齊成 'trendingup'。 */
function normalizeIconName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '')
}

export function EpisodeCover({
  episodeId,
  size,
  topic,
  coverIcon,
  imageUrl,
  className = '',
}: EpisodeCoverProps) {
  const art = useMemo(() => getCoverArt(episodeId, topic), [episodeId, topic])

  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        className={`${SIZE_CLASS[size]} object-cover ${className}`}
      />
    )
  }

  const IconComponent =
    (coverIcon && ICON_BY_NAME[normalizeIconName(coverIcon)]) ||
    (topic && TOPIC_DEFAULT_ICON[topic]) ||
    Radio

  return (
    <div
      className={`relative overflow-hidden shrink-0 border border-white/15 shadow-[inset_0_1px_1px_rgba(255,255,255,0.35),0_8px_20px_-4px_rgba(0,0,0,0.4)] ${SIZE_CLASS[size]} ${className}`}
    >
      {/* 1. Fluid Mesh 漸層底圖 */}
      <div className="absolute inset-0 transition-opacity duration-700" style={{ background: coverArtBackground(art) }} />

      {/* 2. 背景浮雕浮水印 Icon (Ambient Overlay Watermark, 融入背景流體層) */}
      <div className="absolute -right-2 -bottom-2 opacity-20 mix-blend-overlay text-white pointer-events-none transform -rotate-12 select-none">
        <IconComponent size={WATERMARK_SIZE[size]} strokeWidth={1.5} />
      </div>

      {/* 3. 動態流體 Ambient Glow 暈染球 (Apple Music 氛圍光效) */}
      <div
        className="absolute rounded-full blur-2xl opacity-60 mix-blend-screen pointer-events-none animate-pulse"
        style={{
          left: `${art.blobX}%`,
          top: `${art.blobY}%`,
          width: `${art.blobSize}%`,
          height: `${art.blobSize}%`,
          background: art.stops[2],
          transform: 'translate(-50%, -50%)',
          animationDuration: '6s',
        }}
      />
      <div
        className="absolute rounded-full blur-3xl opacity-40 mix-blend-color-dodge pointer-events-none"
        style={{
          left: `${100 - art.blobX}%`,
          top: `${100 - art.blobY}%`,
          width: `${art.blobSize * 0.8}%`,
          height: `${art.blobSize * 0.8}%`,
          background: art.stops[1],
          transform: 'translate(-50%, -50%)',
        }}
      />

      {/* 4. 頂部玻璃反光鏡面 (Apple Glass Specular Rim Light) */}
      <div className="absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-white/20 via-white/5 to-transparent pointer-events-none" />

      {/* 5. 菲林微顆粒感 (Film Grain Noise) */}
      <div
        className="absolute inset-0 opacity-30 mix-blend-overlay pointer-events-none"
        style={{ backgroundImage: `url("${COVER_GRAIN_URL}")` }}
      />
    </div>
  )
}


