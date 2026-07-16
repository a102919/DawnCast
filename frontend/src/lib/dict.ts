import type { DictEntry } from '../api/types'

export function lemmatize(word: string): string[] {
  const w = word.toLowerCase()
  const candidates = [w]

  // -ing → base (running → run, writing → write)
  if (w.endsWith('ing')) {
    candidates.push(w.slice(0, -3))
    candidates.push(w.slice(0, -3) + 'e')
    if (w.length > 5) candidates.push(w.slice(0, -4)) // doubling (running → run)
  }
  // -ed → base
  if (w.endsWith('ed')) {
    candidates.push(w.slice(0, -2))
    candidates.push(w.slice(0, -1))
    if (w.length > 4) candidates.push(w.slice(0, -3)) // doubling
  }
  // -s / -es
  if (w.endsWith('ies')) candidates.push(w.slice(0, -3) + 'y')
  if (w.endsWith('es')) candidates.push(w.slice(0, -2))
  if (w.endsWith('s') && !w.endsWith('ss')) candidates.push(w.slice(0, -1))
  // -er / -est → base
  if (w.endsWith('er')) candidates.push(w.slice(0, -2))
  if (w.endsWith('est')) candidates.push(w.slice(0, -3))
  // -ly
  if (w.endsWith('ly')) candidates.push(w.slice(0, -2))

  return [...new Set(candidates)]
}

export function lookupDict(
  word: string,
  dict: Record<string, DictEntry>
): { entry: DictEntry; matchedLemma: string } | null {
  for (const lemma of lemmatize(word)) {
    const entry = dict[lemma]
    if (entry) return { entry, matchedLemma: lemma }
  }
  return null
}
