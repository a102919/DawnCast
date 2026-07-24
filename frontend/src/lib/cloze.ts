export interface ClozeParts {
  readonly before: string
  readonly blank: string
  readonly after: string
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** 用語境句挖空目標單字（大小寫不敏感，word-boundary match）。抓不到回 null，呼叫端 fallback。 */
export function buildCloze(sentence: string, word: string): ClozeParts | null {
  const pattern = new RegExp(`\\b${escapeRegExp(word)}\\b`, 'i')
  const match = pattern.exec(sentence)
  if (!match) return null
  return {
    before: sentence.slice(0, match.index),
    blank: match[0],
    after: sentence.slice(match.index + match[0].length),
  }
}

export function checkClozeAnswer(input: string, target: string): boolean {
  return input.trim().toLowerCase() === target.trim().toLowerCase()
}
