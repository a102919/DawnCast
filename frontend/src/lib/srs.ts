import type { VocabItem } from '../api/types'
import { toIsoDate } from './dailyOrderDate'

/** 單字生命週期（migration 0026）：1=新字(待學習) 2=複習中(SRS) 5=精熟封存。
 *  畢業候選不落 DB，純推導：status 2 且 interval >= GRADUATION_INTERVAL 且到期。 */
export const STATUS_NEW = 1
export const STATUS_REVIEW = 2
export const MASTERED_STATUS = 5

/** 複習間隔達此天數即解鎖畢業測驗（連續 2 輪通過 → 精熟）。 */
export const GRADUATION_INTERVAL = 21

/** 學習模式每 session 上限（分塊學習，結算頁可續學）。 */
export const LEARN_SESSION_LIMIT = 10

/** 過渡期容錯：舊資料複習過但 status 仍是 1（migration 0026 backfill 前、或
 *  mock localStorage 舊資料）。判定同 backfill：interval/ease 偏離初始值即視為複習中。 */
function effectiveStatus(item: VocabItem): number {
  const status = item.status ?? STATUS_NEW
  if (status === STATUS_NEW && ((item.interval ?? 1) > 1 || (item.ease ?? 2.5) !== 2.5)) {
    return STATUS_REVIEW
  }
  return status
}

export function isDue(item: VocabItem, today: string = toIsoDate(new Date())): boolean {
  if (effectiveStatus(item) !== STATUS_REVIEW) return false
  return !item.nextReview || item.nextReview <= today
}

function sortByNextReview(due: readonly VocabItem[]): readonly VocabItem[] {
  return [...due].sort((a, b) => {
    if (!a.nextReview && !b.nextReview) return 0
    if (!a.nextReview) return -1
    if (!b.nextReview) return 1
    return a.nextReview.localeCompare(b.nextReview)
  })
}

/** 學習佇列：尚未通過學習模式的新字，最早收錄優先。
 *  完整回傳（入口卡顯示總數用）；session 端自行 slice LEARN_SESSION_LIMIT 分塊。 */
export function filterLearnDeck(items: readonly VocabItem[]): readonly VocabItem[] {
  return items
    .filter(item => effectiveStatus(item) === STATUS_NEW)
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt))
}

/** 滑卡複習佇列：到期且尚未達畢業門檻的字。達門檻的字改進畢業測驗（同天同字不重複操練）。 */
export function filterReviewDeck(items: readonly VocabItem[]): readonly VocabItem[] {
  const today = toIsoDate(new Date())
  return sortByNextReview(
    items.filter(item => isDue(item, today) && (item.interval ?? 1) < GRADUATION_INTERVAL),
  )
}

/** 畢業測驗佇列：到期且間隔已達畢業門檻的候選字。 */
export function filterQuizDeck(items: readonly VocabItem[]): readonly VocabItem[] {
  const today = toIsoDate(new Date())
  return sortByNextReview(
    items.filter(item => isDue(item, today) && (item.interval ?? 1) >= GRADUATION_INTERVAL),
  )
}

/** 自由練習池：所有複習中的字，不受到期日限制——練習不呼叫 updateCardReview，不影響 SRS 排程。 */
export function filterPracticePool(items: readonly VocabItem[]): readonly VocabItem[] {
  return sortByNextReview(items.filter(item => effectiveStatus(item) === STATUS_REVIEW))
}

/** 三佇列待辦總數（BottomNav badge / 播放完提醒用）；學習佇列不受 session 上限截斷。 */
export function countActionable(items: readonly VocabItem[]): number {
  const today = toIsoDate(new Date())
  const learn = items.filter(item => effectiveStatus(item) === STATUS_NEW).length
  const due = items.filter(item => isDue(item, today)).length
  return learn + due
}
