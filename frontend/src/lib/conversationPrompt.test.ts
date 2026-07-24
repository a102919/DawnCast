import { describe, expect, it } from 'vitest'
import { buildConversationPrompt } from './conversationPrompt'

const cues = [
  { index: 0, speaker: 'Alex', text: 'Hello everyone.', zh: '大家好。', start: 0, end: 2 },
  { index: 1, speaker: 'Sarah', text: "Let's talk about quantum computing.", zh: '我們來聊聊量子計算。', start: 2, end: 5 },
]

describe('buildConversationPrompt', () => {
  it('內嵌標題、CEFR 等級與英文逐句（不含中文）', () => {
    const prompt = buildConversationPrompt({
      episodeTitle: 'Quantum Computing 101',
      cues,
      cefrLevel: 'B1',
      vocab: [],
    })
    expect(prompt).toContain('Quantum Computing 101')
    expect(prompt).toContain('CEFR B1')
    expect(prompt).toContain('Alex: Hello everyone.')
    expect(prompt).toContain("Sarah: Let's talk about quantum computing.")
    expect(prompt).not.toContain('大家好')
  })

  it('A2 等級用簡化難度指示，且與 B2 不同', () => {
    const beginner = buildConversationPrompt({ episodeTitle: 'X', cues, cefrLevel: 'A2', vocab: [] })
    const advanced = buildConversationPrompt({ episodeTitle: 'X', cues, cefrLevel: 'B2', vocab: [] })
    expect(beginner).toContain('避免片語動詞和俚語')
    expect(advanced).toContain('慣用語')
    expect(beginner).not.toBe(advanced)
  })

  it('沒有單字時不出現單字練習段落', () => {
    const prompt = buildConversationPrompt({ episodeTitle: 'X', cues, cefrLevel: 'A2', vocab: [] })
    expect(prompt).not.toContain('特別練習使用這些單字')
  })

  it('有單字時列出單字清單', () => {
    const prompt = buildConversationPrompt({
      episodeTitle: 'X',
      cues,
      cefrLevel: 'B2',
      vocab: [{ word: 'quantum', translation: '量子' }, { word: 'entanglement', translation: '糾纏' }],
    })
    expect(prompt).toContain('特別練習使用這些單字：quantum、entanglement')
  })
})
