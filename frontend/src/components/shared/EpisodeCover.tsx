import { useMemo } from 'react'
import { getCoverArt, coverArtBackground, COVER_GRAIN_URL } from '../../lib'

type CoverSize = 'sm' | 'md' | 'lg' | 'hero'

interface EpisodeCoverProps {
  readonly episodeId: string
  readonly size: CoverSize
  /** 未來接真封面圖：有值時直接 render <img>，不跑生成邏輯。 */
  readonly imageUrl?: string | null
  readonly className?: string
}

const SIZE_CLASS: Record<CoverSize, string> = {
  sm: 'w-10 h-10 rounded-md',
  md: 'w-16 h-16 rounded-lg',
  lg: 'w-28 h-28 rounded-xl',
  hero: 'w-full aspect-square rounded-2xl',
}

export function EpisodeCover({ episodeId, size, imageUrl, className = '' }: EpisodeCoverProps) {
  const art = useMemo(() => getCoverArt(episodeId), [episodeId])

  if (imageUrl) {
    return (
      <img
        src={imageUrl}
        alt=""
        className={`${SIZE_CLASS[size]} object-cover ${className}`}
      />
    )
  }

  return (
    <div className={`relative overflow-hidden shrink-0 ${SIZE_CLASS[size]} ${className}`}>
      <div className="absolute inset-0" style={{ background: coverArtBackground(art) }} />
      <div
        className="absolute rounded-full blur-2xl opacity-40"
        style={{
          left: `${art.blobX}%`,
          top: `${art.blobY}%`,
          width: `${art.blobSize}%`,
          height: `${art.blobSize}%`,
          background: art.stops[2],
          transform: 'translate(-50%, -50%)',
        }}
      />
      <div
        className="absolute inset-0 opacity-40 mix-blend-overlay"
        style={{ backgroundImage: `url("${COVER_GRAIN_URL}")` }}
      />
    </div>
  )
}
