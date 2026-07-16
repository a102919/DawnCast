export type Cue = {
  readonly start: number
  readonly end: number
}

export function findActiveCueIndex(cues: readonly Cue[], currentTime: number): number {
  if (cues.length === 0) return -1

  let lo = 0
  let hi = cues.length - 1

  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const cue = cues[mid]

    if (currentTime < cue.start) {
      hi = mid - 1
    } else if (currentTime > cue.end) {
      lo = mid + 1
    } else {
      return mid
    }
  }

  // 若在兩個 cue 之間，回傳最近的前一個
  if (hi >= 0) return hi
  return -1
}

export function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
