import type { VocabItem } from '../api/types'
import { buildCloze } from './cloze'
import { toIsoDate } from './dailyOrderDate'
import { MASTERED_STATUS } from './srs'

/** 畢業測驗題型：英→中選擇、中→英選擇、聽力選義、例句拼字填空。 */
export type QuizKind = 'en2zh' | 'zh2en' | 'listening' | 'cloze'

export type ChoiceOption = {
  /** 選項來源字的 vocab id（正解即題目字本身的 id） */
  readonly id: string
  readonly label: string
}

export type QuizQuestion =
  | {
      readonly kind: 'en2zh' | 'zh2en' | 'listening'
      readonly item: VocabItem
      /** 題面：en2zh/listening 顯示或播 word，zh2en 顯示 translation */
      readonly prompt: string
      readonly options: readonly ChoiceOption[]
      readonly answerId: string
    }
  | {
      readonly kind: 'cloze'
      readonly item: VocabItem
      readonly sentence: string
    }

export type QuizRoundPatch = {
  readonly quizPassStreak: number
  readonly status?: number
  readonly nextReview?: string
  readonly interval?: number
}

/** 連續通過此輪數即精熟。 */
export const QUIZ_PASS_TARGET = 2
/** 通過第 1 輪後，隔這麼多天考第 2 輪（固定間隔，不走 sm2 成長，讓兩輪落在不同週）。 */
const NEXT_ROUND_DAYS = 7
/** 該輪失敗後，隔這麼多天回滑卡複習佇列。 */
const FAIL_RETRY_DAYS = 3
/** 每字每輪出題數。 */
export const QUESTIONS_PER_ROUND = 2

function clozeSentence(item: VocabItem): string | null {
  const sentence = item.sourceSentence || item.exampleEn
  if (!sentence) return null
  return buildCloze(sentence, item.word) ? sentence : null
}

export function availableKinds(item: VocabItem): readonly QuizKind[] {
  const kinds: QuizKind[] = ['en2zh', 'zh2en', 'listening']
  if (clozeSentence(item)) kinds.push('cloze')
  return kinds
}

function shuffle<T>(list: readonly T[], rng: () => number): T[] {
  const out = [...list]
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

/** 干擾項：從單字本其他字抽 3 個。優先同詞性（pos 首字母），排除同 lemma／同翻譯；
 *  不足放寬到任意其他字；仍不足就降級出 2–3 選項。 */
export function pickDistractors(
  item: VocabItem,
  pool: readonly VocabItem[],
  rng: () => number = Math.random,
): readonly VocabItem[] {
  const candidates = pool.filter(
    v => v.id !== item.id && v.lemma !== item.lemma && v.translation !== item.translation,
  )
  const posInitial = item.pos?.[0]
  const samePos = posInitial ? candidates.filter(v => v.pos?.[0] === posInitial) : []
  const ranked = [...shuffle(samePos, rng), ...shuffle(candidates.filter(v => !samePos.includes(v)), rng)]
  // 干擾項彼此的翻譯也要相異，不然兩個「正確答案長一樣」的選項會互相穿幫
  const picked: VocabItem[] = []
  for (const v of ranked) {
    if (picked.length >= 3) break
    if (picked.some(p => p.translation === v.translation || p.lemma === v.lemma)) continue
    picked.push(v)
  }
  return picked
}

function buildChoiceQuestion(
  kind: 'en2zh' | 'zh2en' | 'listening',
  item: VocabItem,
  pool: readonly VocabItem[],
  rng: () => number,
): QuizQuestion {
  const distractors = pickDistractors(item, pool, rng)
  const toLabel = (v: VocabItem) => (kind === 'zh2en' ? v.word : v.translation)
  const options = shuffle(
    [{ id: item.id, label: toLabel(item) }, ...distractors.map(v => ({ id: v.id, label: toLabel(v) }))],
    rng,
  )
  const prompt = kind === 'zh2en' ? item.translation : item.word
  return { kind, item, prompt, options, answerId: item.id }
}

/** 為一個候選字組出這一輪的題目：從可用題型隨機抽 QUESTIONS_PER_ROUND 種不重複。 */
export function buildQuizRound(
  item: VocabItem,
  pool: readonly VocabItem[],
  rng: () => number = Math.random,
): readonly QuizQuestion[] {
  const kinds = shuffle(availableKinds(item), rng).slice(0, QUESTIONS_PER_ROUND)
  return kinds.map(kind => {
    if (kind === 'cloze') {
      const sentence = clozeSentence(item)
      if (sentence) return { kind: 'cloze', item, sentence } satisfies QuizQuestion
      // clozeSentence 在 availableKinds 已過濾，這裡理論上到不了；防禦性降級成選擇題
      return buildChoiceQuestion('en2zh', item, pool, rng)
    }
    return buildChoiceQuestion(kind, item, pool, rng)
  })
}

function plusDays(today: string, days: number): string {
  const d = new Date(`${today}T00:00:00`)
  d.setDate(d.getDate() + days)
  return toIsoDate(d)
}

/** 一輪結果 → PATCH payload。
 *  全對：streak+1；到 QUIZ_PASS_TARGET 即精熟（status=5，nextReview 留舊值無妨——
 *  isDue 先看 status，精熟字永不到期）；否則 7 天後考下一輪。
 *  有錯：streak 歸零、interval 減半（下限 7，多半降回畢業門檻以下）、3 天後回複習佇列。 */
export function applyQuizRound(
  item: VocabItem,
  passed: boolean,
  today: string = toIsoDate(new Date()),
): QuizRoundPatch {
  if (passed) {
    const streak = (item.quizPassStreak ?? 0) + 1
    if (streak >= QUIZ_PASS_TARGET) return { quizPassStreak: QUIZ_PASS_TARGET, status: MASTERED_STATUS }
    return { quizPassStreak: streak, nextReview: plusDays(today, NEXT_ROUND_DAYS) }
  }
  return {
    quizPassStreak: 0,
    interval: Math.max(7, Math.floor((item.interval ?? 21) / 2)),
    nextReview: plusDays(today, FAIL_RETRY_DAYS),
  }
}
