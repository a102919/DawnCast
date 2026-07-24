import { describe, expect, it } from 'vitest'
import { isDue, filterDueDeck, MASTERED_STATUS } from './srs'
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
    ...overrides,
  }
}

describe('isDue', () => {
  it('沒有 nextReview 的新卡視為到期', () => {
    expect(isDue(makeItem(), '2026-07-24')).toBe(true)
  })

  it('nextReview 早於或等於今天視為到期', () => {
    expect(isDue(makeItem({ nextReview: '2026-07-24' }), '2026-07-24')).toBe(true)
    expect(isDue(makeItem({ nextReview: '2026-07-01' }), '2026-07-24')).toBe(true)
  })

  it('nextReview 晚於今天不算到期', () => {
    expect(isDue(makeItem({ nextReview: '2026-08-01' }), '2026-07-24')).toBe(false)
  })

  it('已精熟（status=5）的卡不管日期都不算到期', () => {
    expect(isDue(makeItem({ status: MASTERED_STATUS }), '2026-07-24')).toBe(false)
  })
})

function daysFromNow(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toLocaleDateString('en-CA')
}

describe('filterDueDeck', () => {
  it('排除未到期與已精熟的卡，其餘依 nextReview 由早到晚排序', () => {
    const items = [
      makeItem({ id: 'a', nextReview: daysFromNow(30) }), // 未到期
      makeItem({ id: 'b', nextReview: daysFromNow(-10) }),
      makeItem({ id: 'c', status: MASTERED_STATUS }), // 已精熟
      makeItem({ id: 'd' }), // 沒有 nextReview，視為最先到期
    ]
    const deck = filterDueDeck(items)
    expect(deck.map(i => i.id)).toEqual(['d', 'b'])
  })
})
