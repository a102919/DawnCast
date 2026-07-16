export type WordToken = {
  readonly word: string
  readonly raw: string
  readonly start: number
  readonly end: number
  readonly isStop: boolean
}

const STOP_WORDS = new Set([
  'a', 'an', 'the', 'is', 'it', 'in', 'on', 'at', 'to', 'of', 'for',
  'and', 'or', 'but', 'not', 'be', 'as', 'are', 'was', 'were', 'has',
  'have', 'had', 'do', 'does', 'did', 'will', 'would', 'can', 'could',
  'should', 'may', 'might', 'i', 'you', 'he', 'she', 'we', 'they',
  'this', 'that', 'with', 'from', 'by', 'so', 'if', 'up', 'out',
  'no', 'my', 'your', 'its', 'our', 'their', 'me', 'him', 'her', 'us',
  'them', 'what', 'which', 'who', 'how', 'when', 'where', 'why',
  'all', 'any', 'both', 'each', 'few', 'more', 'most', 'other', 'some',
  'such', 'than', 'then', 'too', 'very', 'just', 'about', 'after',
  'before', 'between', 'into', 'through', 'during', 'also', 'over',
  'under', 'again', 'there', 'here', 'been', 'being',
])

export function splitTextToWords(text: string): WordToken[] {
  const tokens: WordToken[] = []
  const regex = /[a-zA-Z']+/g
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    const raw = match[0]
    const word = raw.replace(/^'+|'+$/g, '').toLowerCase()
    if (!word) continue
    tokens.push({
      word,
      raw,
      start: match.index,
      end: match.index + raw.length,
      isStop: STOP_WORDS.has(word),
    })
  }

  return tokens
}
