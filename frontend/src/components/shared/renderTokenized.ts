import type { ReactNode, MouseEvent } from 'react'
import { createElement } from 'react'
import type { WordToken } from '../../lib'

interface RenderTokenizedOptions {
  readonly stopPropagation?: boolean
  readonly nonVocabHoverClass?: string
}

export function renderTokenized(
  text: string,
  tokens: WordToken[],
  onWordClick: (word: string) => void,
  isInVocab: (lemma: string) => boolean,
  options: RenderTokenizedOptions = {}
): ReactNode[] {
  if (tokens.length === 0) return [text]

  const { stopPropagation = false, nonVocabHoverClass = '' } = options
  const result: ReactNode[] = []
  let cursor = 0

  for (const token of tokens) {
    if (token.start > cursor) {
      result.push(text.slice(cursor, token.start))
    }

    if (token.isStop) {
      result.push(token.raw)
    } else {
      const inVocab = isInVocab(token.word)
      const word = token.word
      const handleClick = (e: MouseEvent<HTMLSpanElement>) => {
        if (stopPropagation) e.stopPropagation()
        onWordClick(word)
      }

      result.push(
        createElement(
          'span',
          {
            key: token.start,
            onClick: handleClick,
            className: `cursor-pointer rounded px-0.5 transition-colors duration-fast hover:bg-accent hover:text-white ${
              inVocab
                ? 'text-accent underline decoration-dotted underline-offset-2'
                : nonVocabHoverClass
            }`,
          },
          token.raw
        )
      )
    }

    cursor = token.end
  }

  if (cursor < text.length) {
    result.push(text.slice(cursor))
  }

  return result
}
