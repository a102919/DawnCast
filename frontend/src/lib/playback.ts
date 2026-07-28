export const RATES = [0.75, 1, 1.25, 1.5] as const
export type PlaybackRate = typeof RATES[number]

export function computeProgress(currentTime: number, duration: number): number {
  return duration > 0 ? (currentTime / duration) * 100 : 0
}
