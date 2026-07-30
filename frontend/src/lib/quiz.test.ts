import { describe, expect, it } from 'vitest'
import {
  availableKinds,
  buildQuizRound,
  pickDistractors,
  applyQuizRound,
  QUESTIONS_PER_ROUND,
} from './quiz'
import { MASTERED_STATUS, STATUS_REVIEW } from './srs'
import type { VocabItem } from '../api/types'

function makeItem(overrides: Partial<VocabItem> = {}): VocabItem {
  return {
    id: 'id-1',
    word: 'resilient',
    lemma: 'resilient',
    pos: 'a.',
    translation: '有韌性的',
    sourceEpisodeId: 'ep-1',
    sourceLineNo: 0,
    sourceTimestamp: 0,
    createdAt: '2026-01-01T00:00:00Z',
    senseIdx: 0,
    status: STATUS_REVIEW,
    interval: 21,
    quizPassStreak: 0,
    ...overrides,
  }
}

// 固定 rng：讓 shuffle/抽選可重現
const rngZero = () => 0

describe('availableKinds', () => {
  it('沒有例句時不出拼字題', () => {
    expect(availableKinds(makeItem())).toEqual(['en2zh', 'zh2en', 'listening'])
  })

  it('sourceSentence 或 exampleEn 可挖空時納入拼字題', () => {
    expect(availableKinds(makeItem({ sourceSentence: 'She is resilient.' }))).toContain('cloze')
    expect(availableKinds(makeItem({ exampleEn: 'A resilient person.' }))).toContain('cloze')
  })

  it('例句不含目標字（挖不了空）不出拼字題', () => {
    expect(availableKinds(makeItem({ sourceSentence: 'Totally unrelated.' }))).not.toContain('cloze')
  })
})

describe('pickDistractors', () => {
  const pool = [
    makeItem({ id: 'self', word: 'resilient', lemma: 'resilient' }),
    makeItem({ id: 'd1', word: 'brittle', lemma: 'brittle', translation: '脆的', pos: 'a.' }),
    makeItem({ id: 'd2', word: 'run', lemma: 'run', translation: '跑', pos: 'v.' }),
    makeItem({ id: 'd3', word: 'sturdy', lemma: 'sturdy', translation: '堅固的', pos: 'a.' }),
    makeItem({ id: 'd4', word: 'dog', lemma: 'dog', translation: '狗', pos: 'n.' }),
  ]

  it('抽 3 個、排除自己與同翻譯，優先同詞性', () => {
    const picked = pickDistractors(pool[0], pool, rngZero)
    expect(picked).toHaveLength(3)
    expect(picked.map(v => v.id)).not.toContain('self')
    // 同詞性 a. 的 d1/d3 應優先入選
    expect(picked.map(v => v.id)).toEqual(expect.arrayContaining(['d1', 'd3']))
  })

  it('排除相同 lemma 與相同 translation', () => {
    const withDupes = [
      ...pool,
      makeItem({ id: 'dupe-lemma', lemma: 'resilient', translation: '別的' }),
      makeItem({ id: 'dupe-zh', lemma: 'other', translation: '有韌性的' }),
    ]
    const picked = pickDistractors(pool[0], withDupes, rngZero)
    expect(picked.map(v => v.id)).not.toContain('dupe-lemma')
    expect(picked.map(v => v.id)).not.toContain('dupe-zh')
  })

  it('候選不足時降級出少於 3 個', () => {
    const tiny = [pool[0], pool[1]]
    expect(pickDistractors(pool[0], tiny, rngZero)).toHaveLength(1)
  })
})

describe('buildQuizRound', () => {
  const pool = [
    makeItem({ id: 'self' }),
    makeItem({ id: 'd1', word: 'brittle', lemma: 'brittle', translation: '脆的' }),
    makeItem({ id: 'd2', word: 'run', lemma: 'run', translation: '跑' }),
    makeItem({ id: 'd3', word: 'sturdy', lemma: 'sturdy', translation: '堅固的' }),
  ]

  it('每輪出 QUESTIONS_PER_ROUND 題、題型不重複', () => {
    const round = buildQuizRound(pool[0], pool, rngZero)
    expect(round).toHaveLength(QUESTIONS_PER_ROUND)
    expect(new Set(round.map(q => q.kind)).size).toBe(QUESTIONS_PER_ROUND)
  })

  it('選擇題含正解且選項標籤對應題型方向', () => {
    const round = buildQuizRound(pool[0], pool, rngZero)
    for (const q of round) {
      if (q.kind === 'cloze') continue
      expect(q.options.some(o => o.id === q.answerId)).toBe(true)
      const answer = q.options.find(o => o.id === q.answerId)
      if (q.kind === 'zh2en') {
        expect(q.prompt).toBe('有韌性的')
        expect(answer?.label).toBe('resilient')
      } else {
        expect(q.prompt).toBe('resilient')
        expect(answer?.label).toBe('有韌性的')
      }
    }
  })
})

describe('applyQuizRound', () => {
  it('全對第 1 輪：streak=1、7 天後考第 2 輪', () => {
    const patch = applyQuizRound(makeItem({ quizPassStreak: 0 }), true, '2026-07-30')
    expect(patch).toEqual({ quizPassStreak: 1, nextReview: '2026-08-06' })
  })

  it('全對第 2 輪：精熟封存', () => {
    const patch = applyQuizRound(makeItem({ quizPassStreak: 1 }), true, '2026-07-30')
    expect(patch).toEqual({ quizPassStreak: 2, status: MASTERED_STATUS })
  })

  it('有錯：streak 歸零、interval 減半（下限 7）、3 天後回複習', () => {
    expect(applyQuizRound(makeItem({ interval: 30, quizPassStreak: 1 }), false, '2026-07-30')).toEqual({
      quizPassStreak: 0,
      interval: 15,
      nextReview: '2026-08-02',
    })
    expect(applyQuizRound(makeItem({ interval: 8 }), false, '2026-07-30').interval).toBe(7)
  })
})
