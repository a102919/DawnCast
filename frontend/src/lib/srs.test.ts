import { describe, expect, it } from 'vitest'
import {
  isDue,
  filterLearnDeck,
  filterReviewDeck,
  filterQuizDeck,
  filterPracticePool,
  countActionable,
  buildSessionSteps,
  pickReviewKind,
  canClozeItem,
  GRADUATION_INTERVAL,
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

describe('canClozeItem / pickReviewKind', () => {
  it('有 exampleEn 且挖得到空 → 可 cloze', () => {
    expect(canClozeItem(makeItem({ word: 'test', exampleEn: 'this is a test' }))).toBe(true)
  })

  it('缺 exampleEn → 不可 cloze', () => {
    expect(canClozeItem(makeItem({ word: 'test' }))).toBe(false)
  })

  it('exampleEn 沒有目標單字 → 不可 cloze', () => {
    expect(canClozeItem(makeItem({ word: 'test', exampleEn: 'no match here' }))).toBe(false)
  })

  it('pickReviewKind 預設走 recognize，無例句時強制 recognize', () => {
    expect(pickReviewKind(makeItem({ word: 'test' }))).toBe('recognize')
  })

  it('defaultMode=cloze 且可挖空才走 cloze', () => {
    const withCloze = makeItem({ word: 'test', exampleEn: 'a test sentence' })
    expect(pickReviewKind(withCloze, 'cloze')).toBe('cloze')
    expect(pickReviewKind(withCloze, 'recognize')).toBe('recognize')
    expect(pickReviewKind(makeItem({ word: 'test' }), 'cloze')).toBe('recognize')
  })
})

describe('buildSessionSteps', () => {
  const today = '2026-07-30'

  it('items 為空 → 回空陣列', () => {
    expect(buildSessionSteps([], today)).toEqual([])
  })

  it('純到期複習：升冪排序（null date 排最前；越早到期的越前）、走 recognize', () => {
    const items = [
      makeItem({ id: 'late', nextReview: daysFromNow(-5), interval: 6 }),
      makeItem({ id: 'early', nextReview: daysFromNow(-1), interval: 6 }),
      makeItem({ id: 'no-date', interval: 6 }),
    ]
    expect(buildSessionSteps(items, today).map(s => `${s.kind}:${s.item.id}`)).toEqual([
      'recognize:no-date',
      'recognize:late',
      'recognize:early',
    ])
  })

  it('到期且 interval >= GRADUATION_INTERVAL 走 quiz', () => {
    const items = [
      makeItem({ id: 'grad', nextReview: daysFromNow(-1), interval: GRADUATION_INTERVAL }),
    ]
    expect(buildSessionSteps(items, today)).toEqual([{ kind: 'quiz', item: items[0] }])
  })

  it('到期複習優先於新字', () => {
    const items = [
      makeItem({ id: 'new', status: STATUS_NEW, createdAt: '2026-01-01T00:00:00Z' }),
      makeItem({ id: 'due', nextReview: daysFromNow(-1), interval: 6 }),
    ]
    expect(buildSessionSteps(items, today).map(s => s.item.id)).toEqual(['due', 'new'])
  })

  it('新字超過 SESSION_LIMIT 時截前 10', () => {
    const items = Array.from({ length: LEARN_SESSION_LIMIT + 5 }, (_, i) =>
      makeItem({ id: `n-${i}`, status: STATUS_NEW, createdAt: `2026-01-${String(i + 1).padStart(2, '0')}T00:00:00Z` }),
    )
    const steps = buildSessionSteps(items, today)
    expect(steps).toHaveLength(LEARN_SESSION_LIMIT)
    expect(steps.every(s => s.kind === 'learn')).toBe(true)
  })

  it('無到期無新字時填練習（status=2 非到期，ease 升冪）', () => {
    const items = [
      makeItem({ id: 'easy', nextReview: daysFromNow(10), ease: 2.5, interval: 6 }),
      makeItem({ id: 'weak', nextReview: daysFromNow(10), ease: 1.5, interval: 6 }),
    ]
    const steps = buildSessionSteps(items, today)
    expect(steps.map(s => s.item.id)).toEqual(['weak', 'easy'])
    expect(steps.every(s => s.kind === 'recognize')).toBe(true)
  })

  it('非到期的 status=2 字填練習池（ease 升冪，最弱的排最前）', () => {
    const items = [
      makeItem({ id: 'easy', nextReview: daysFromNow(10), ease: 2.5, interval: 6 }),
      makeItem({ id: 'weak', nextReview: daysFromNow(10), ease: 1.5, interval: 6 }),
    ]
    const steps = buildSessionSteps(items, today)
    expect(steps.map(s => s.item.id)).toEqual(['weak', 'easy'])
    expect(steps.every(s => s.kind === 'recognize')).toBe(true)
  })

  it('保底：所有字都進不了任何分支時不應回空陣列以外的東西（items 全空時回空）', () => {
    expect(buildSessionSteps([], today)).toEqual([])
  })

  it('精熟（status=5）永不進佇列', () => {
    const items = [makeItem({ id: 'mastered', status: MASTERED_STATUS })]
    expect(buildSessionSteps(items, today)).toEqual([])
  })
})
