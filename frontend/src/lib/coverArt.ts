/** 依 episode id deterministic 生成抽象封面藝術，純 CSS，無外部圖檔/依賴。 */

function hashStringToSeed(str: string): number {
  let h = 2166136261
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function mulberry32(seed: number) {
  let a = seed
  return function rand() {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** 8 組精選三階漸層色票（深→中→淺），避免隨機色相產生醜配色。 */
const PALETTES: readonly (readonly [string, string, string])[] = [
  ['#2B1704', '#C2410C', '#FBBF24'], // Dawn Ember
  ['#3F1D2B', '#C2416B', '#F4A6C1'], // Rose Quartz
  ['#031A2E', '#0E6BA8', '#7FD1E0'], // Deep Ocean
  ['#1E0F2E', '#6B3FA0', '#C9A0FF'], // Plum Dusk
  ['#0E2318', '#2F6B4F', '#A8D5A0'], // Forest Moss
  ['#12161C', '#3E4C5E', '#9FB3C8'], // Slate Steel
  ['#2A1400', '#B8722E', '#FFD08A'], // Golden Hour
  ['#0D0B21', '#4A3F8C', '#9F8FE0'], // Ink Violet
]

export interface CoverArt {
  readonly stops: readonly [string, string, string]
  readonly angle: number
  readonly posX: number
  readonly posY: number
  readonly blobX: number
  readonly blobY: number
  readonly blobSize: number
}

export function getCoverArt(episodeId: string): CoverArt {
  const rand = mulberry32(hashStringToSeed(episodeId))
  const stops = PALETTES[Math.floor(rand() * PALETTES.length)]
  return {
    stops,
    angle: Math.floor(rand() * 360),
    posX: 20 + rand() * 60,
    posY: 20 + rand() * 60,
    blobX: 15 + rand() * 70,
    blobY: 15 + rand() * 70,
    blobSize: 40 + rand() * 30,
  }
}

/** conic 掃色 + radial 底色疊層組成的 CSS background 字串。 */
export function coverArtBackground(art: CoverArt): string {
  const [c1, c2, c3] = art.stops
  return [
    `conic-gradient(from ${art.angle}deg at ${art.posX}% ${art.posY}%, transparent 0deg, ${c2}66 90deg, transparent 180deg)`,
    `radial-gradient(circle at ${art.posX}% ${art.posY}%, ${c3} 0%, ${c2} 45%, ${c1} 100%)`,
  ].join(', ')
}

/** 固定顆粒紋理（feTurbulence），與漸層本身無關，所有封面共用同一張疊在最上層做質感。 */
export const COVER_GRAIN_URL =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='matrix' values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.05 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E"
