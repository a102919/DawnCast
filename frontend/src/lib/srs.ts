import type { VocabItem } from '../api/types'
import { toIsoDate } from './dailyOrderDate'
import { canBuildCloze } from './cloze'

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

// ---------- 智慧佇列（單一 session，「開始學習」CTA 用） ----------

/** 單字可不可以走拼字 cloze 題（exampleEn 有且單字挖得到空）。 */
export function canClozeItem(item: VocabItem): boolean {
  return canBuildCloze(item)
}

/** 進階複習模式名（沿用既有 FlashcardRoute `dawncast:flashcards:mode` storage）。
 *  字串型別而非 union：localStorage 內容不可信，runtime 容錯一律 fallback。 */
export type ReviewMode = 'recognize' | 'cloze'

/** 為 status=2 字決定本次走翻卡辨識還是拼字 cloze。
 *  預設 recognize（避免沒例句時 cloze 卡住）；
 *  注入 defaultMode 讓 component 從 storage 讀進來、純函式不碰瀏覽器 API。 */
export function pickReviewKind(
  item: VocabItem,
  defaultMode: ReviewMode = 'recognize',
): 'recognize' | 'cloze' {
  if (defaultMode === 'cloze' && canClozeItem(item)) return 'cloze'
  return 'recognize'
}

/** 智慧佇列中一張卡對應的題型。`quiz` 內部展開成 2 題 round（沿用 buildQuizRound）。 */
export type SessionStep =
  | { readonly kind: 'learn'; readonly item: VocabItem }
  | { readonly kind: 'recognize'; readonly item: VocabItem }
  | { readonly kind: 'cloze'; readonly item: VocabItem }
  | { readonly kind: 'quiz'; readonly item: VocabItem }

const SESSION_LIMIT = LEARN_SESSION_LIMIT

/** 單一陣列串接的智慧佇列建構函式（不要三段拼接再 sort）。
 *  排序規則（依序填入，總數 <= SESSION_LIMIT）：
 *   1. 到期複習卡（含畢業候選 kind='quiz'）→ nextReview 升冪
 *   2. 新字 → createdAt 升冪（最多 SESSION_LIMIT 個）
 *   3. 加強練習填充：status=2 非到期 → ease 升冪、nextReview 升冪
 *   4. 保底：以上皆空且 items 非空時，從 status=2 挑 ease 最低的 1 張
 *   5. items 完全空 → 回空陣列（呼叫端處理空狀態）
 *
 *  `today` 注入以利測試；純函式：不碰 localStorage、API、不改傳入的 items。 */
export function buildSessionSteps(
  items: readonly VocabItem[],
  today: string = toIsoDate(new Date()),
): readonly SessionStep[] {
  if (items.length === 0) return []

  const due = items
    .filter(item => isDue(item, today) && effectiveStatus(item) === STATUS_REVIEW)
    .sort((a, b) => {
      if (!a.nextReview && !b.nextReview) return 0
      if (!a.nextReview) return -1
      if (!b.nextReview) return 1
      return a.nextReview.localeCompare(b.nextReview)
    })
  const learned = items
    .filter(item => effectiveStatus(item) === STATUS_NEW)
    .sort((a, b) => a.createdAt.localeCompare(b.createdAt))
    .slice(0, SESSION_LIMIT)
  const practice = items
    .filter(item => effectiveStatus(item) === STATUS_REVIEW && !isDue(item, today))
    .sort((a, b) => {
      const ae = a.ease ?? 2.5
      const be = b.ease ?? 2.5
      if (ae !== be) return ae - be
      const an = a.nextReview ?? '9999-12-31'
      const bn = b.nextReview ?? '9999-12-31'
      return an.localeCompare(bn)
    })
  // ponytail: 已排序的 due 全部留下、new 補到 SESSION_LIMIT、不夠才進練習池——
  // 學理上「同字本非空必有一個 status=2 可用」保證佇列≥1。
  const dueSteps: SessionStep[] = due.map(item => ((item.interval ?? 1) >= GRADUATION_INTERVAL
    ? { kind: 'quiz' as const, item }
    : { kind: 'recognize' as const, item }))
  const newSteps: SessionStep[] = learned.map(item => ({ kind: 'learn' as const, item }))

  // 組合：due 全部留下、new 補到 SESSION_LIMIT 為止；剩下空間塞練習。
  const filled: SessionStep[] = [...dueSteps]
  if (filled.length < SESSION_LIMIT) {
    filled.push(...newSteps.slice(0, SESSION_LIMIT - filled.length))
  }
  // 補到 SESSION_LIMIT：先給練習池補；學理上「永不為 0」已由 due 全部留下保證
  // （同字單本非空必有一個 status=2 可用——見 srs.test.ts 對應案例）。
  if (filled.length < SESSION_LIMIT) {
    filled.push(...practice.slice(0, SESSION_LIMIT - filled.length).map(item => ({ kind: 'recognize' as const, item })))
  }

  return filled
}
