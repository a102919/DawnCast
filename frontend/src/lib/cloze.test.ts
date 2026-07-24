import { describe, expect, it } from 'vitest'
import { buildCloze, checkClozeAnswer } from './cloze'

describe('buildCloze', () => {
  it('大小寫不敏感挖空，回傳挖空前後段落', () => {
    const result = buildCloze('The Weather is nice today.', 'weather')
    expect(result).toEqual({ before: 'The ', blank: 'Weather', after: ' is nice today.' })
  })

  it('word-boundary match，不誤挖詞根相同的較長單字', () => {
    const result = buildCloze('The cat sat on the mat.', 'cat')
    expect(result).toEqual({ before: 'The ', blank: 'cat', after: ' sat on the mat.' })
  })

  it('句子裡找不到單字時回 null', () => {
    expect(buildCloze('Nothing matches here.', 'banana')).toBeNull()
  })

  it('單字含正則特殊字元時不會噴錯', () => {
    expect(() => buildCloze('Let\'s go (now).', '(now)')).not.toThrow()
  })
})

describe('checkClozeAnswer', () => {
  it('忽略大小寫與頭尾空白', () => {
    expect(checkClozeAnswer('  Weather ', 'weather')).toBe(true)
  })

  it('不同字判為錯誤', () => {
    expect(checkClozeAnswer('whether', 'weather')).toBe(false)
  })
})
