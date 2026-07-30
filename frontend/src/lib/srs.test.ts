import { describe, expect, it } from 'vitest'
import {
  isDue,
  filterLearnDeck,
  filterReviewDeck,
  filterQuizDeck,
  filterPracticePool,
  countActionable,
  STATUS_NEW,
  STATUS_REVIEW,
  MASTERED_STATUS,
  LEARN_SESSION_LIMIT,
} from './srs'
import type { VocabItem } from '../api/types'

function makeItem(overrides: Partial<VocabItem> = {}): VocabItem {
  return {
    id: 'id-1',
    word: 'test',
    lemma: 'test',
    translation: '測試',
    sourceEpisodeId: 'ep-1',
    sourceLineNo: 0,
    sourceTimestamp: 0,
    createdAt: '2026-01-01T00:00:00Z',
    senseIdx: 0,
    status: STATUS_REVIEW,
    ...overrides,
  }
}

function daysFromNow(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toLocaleDateString('en-CA')
}

describe('isDue', () => {
  it('複習中且沒有 nextReview 視為到期', () => {
    expect(isDue(makeItem(), '2026-07-24')).toBe(true)
  })

  it('nextReview 早於或等於今天視為到期', () => {
    expect(isDue(makeItem({ nextReview: '2026-07-24' }), '2026-07-24')).toBe(true)
    expect(isDue(makeItem({ nextReview: '2026-07-01' }), '2026-07-24')).toBe(true)
  })

  it('nextReview 晚於今天不算到期', () => {
    expect(isDue(makeItem({ nextReview: '2026-08-01' }), '2026-07-24')).toBe(false)
  })

  it('新字（status=1）屬於學習佇列，不算到期', () => {
    expect(isDue(makeItem({ status: STATUS_NEW }), '2026-07-24')).toBe(false)
  })

  it('已精熟（status=5）的卡不管日期都不算到期', () => {
    expect(isDue(makeItem({ status: MASTERED_STATUS }), '2026-07-24')).toBe(false)
  })

  it('過渡期容錯：status=1 但 interval/ease 偏離初始值視為複習中', () => {
    expect(isDue(makeItem({ status: STATUS_NEW, interval: 6 }), '2026-07-24')).toBe(true)
    expect(isDue(makeItem({ status: STATUS_NEW, ease: 1.96 }), '2026-07-24')).toBe(true)
  })
})

describe('filterLearnDeck', () => {
  it('只收新字，最早收錄優先', () => {
    const items = [
      makeItem({ id: 'a', status: STATUS_NEW, createdAt: '2026-01-03T00:00:00Z' }),
      makeItem({ id: 'b', status: STATUS_REVIEW }),
      makeItem({ id: 'c', status: STATUS_NEW, createdAt: '2026-01-01T00:00:00Z' }),
      makeItem({ id: 'd', status: MASTERED_STATUS }),
    ]
    expect(filterLearnDeck(items).map(i => i.id)).toEqual(['c', 'a'])
  })

  it('完整回傳不截斷（session 端自行 slice 上限）', () => {
    const items = Array.from({ length: LEARN_SESSION_LIMIT + 5 }, (_, i) =>
      makeItem({ id: `n-${i}`, status: STATUS_NEW }),
    )
    expect(filterLearnDeck(items)).toHaveLength(LEARN_SESSION_LIMIT + 5)
  })
})

describe('filterReviewDeck / filterQuizDeck', () => {
  it('到期字依畢業門檻分流：interval < 21 進複習、>= 21 進測驗', () => {
    const items = [
      makeItem({ id: 'review', nextReview: daysFromNow(-1), interval: 6 }),
      makeItem({ id: 'quiz', nextReview: daysFromNow(-1), interval: 21 }),
      makeItem({ id: 'future', nextReview: daysFromNow(30), interval: 6 }), // 未到期
      makeItem({ id: 'new', status: STATUS_NEW }), // 學習佇列
      makeItem({ id: 'done', status: MASTERED_STATUS }), // 精熟
    ]
    expect(filterReviewDeck(items).map(i => i.id)).toEqual(['review'])
    expect(filterQuizDeck(items).map(i => i.id)).toEqual(['quiz'])
  })

  it('複習佇列依 nextReview 由早到晚，沒有 nextReview 排最前', () => {
    const items = [
      makeItem({ id: 'a', nextReview: daysFromNow(-1), interval: 3 }),
      makeItem({ id: 'b', nextReview: daysFromNow(-10), interval: 3 }),
      makeItem({ id: 'c', interval: 3 }),
    ]
    expect(filterReviewDeck(items).map(i => i.id)).toEqual(['c', 'b', 'a'])
  })
})

describe('filterPracticePool', () => {
  it('不受到期日限制，複習中的字（含未到期、含畢業門檻）都收', () => {
    const items = [
      makeItem({ id: 'due', nextReview: daysFromNow(-1), interval: 6 }),
      makeItem({ id: 'future', nextReview: daysFromNow(30), interval: 6 }),
      makeItem({ id: 'quiz-ready', nextReview: daysFromNow(30), interval: 21 }),
      makeItem({ id: 'new', status: STATUS_NEW }),
      makeItem({ id: 'done', status: MASTERED_STATUS }),
    ]
    expect(filterPracticePool(items).map(i => i.id).sort()).toEqual(['due', 'future', 'quiz-ready'])
  })
})

describe('countActionable', () => {
  it('學習 + 到期（複習與測驗）總和，學習不受 session 上限截斷', () => {
    const items = [
      ...Array.from({ length: LEARN_SESSION_LIMIT + 2 }, (_, i) =>
        makeItem({ id: `n-${i}`, status: STATUS_NEW }),
      ),
      makeItem({ id: 'review', nextReview: daysFromNow(-1), interval: 6 }),
      makeItem({ id: 'quiz', nextReview: daysFromNow(-1), interval: 30 }),
      makeItem({ id: 'future', nextReview: daysFromNow(5), interval: 6 }),
      makeItem({ id: 'done', status: MASTERED_STATUS }),
    ]
    expect(countActionable(items)).toBe(LEARN_SESSION_LIMIT + 2 + 2)
  })
})
