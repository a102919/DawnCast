import type { VocabItem } from '../api/types'
import { toIsoDate } from './dailyOrderDate'

/** 已精熟卡片不再進入待複習輪替（見 VocabProvider.updateCardReview 的門檻判斷）。 */
export const MASTERED_STATUS = 5

export function isDue(item: VocabItem, today: string = toIsoDate(new Date())): boolean {
  if (item.status === MASTERED_STATUS) return false
  return !item.nextReview || item.nextReview <= today
}

export function filterDueDeck(items: readonly VocabItem[]): readonly VocabItem[] {
  if (items.length === 0) return []
  const today = toIsoDate(new Date())
  const due = items.filter(item => isDue(item, today))
  return [...due].sort((a, b) => {
    if (!a.nextReview && !b.nextReview) return 0
    if (!a.nextReview) return -1
    if (!b.nextReview) return 1
    return a.nextReview.localeCompare(b.nextReview)
  })
}
