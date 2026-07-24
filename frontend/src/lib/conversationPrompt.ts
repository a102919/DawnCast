import type { Cue } from '../types/episode'
import type { CefrLevel } from './episode'

const LEVEL_GUIDANCE: Record<CefrLevel, string> = {
  A2: '用最基礎、最生活化的單字和簡短句子（一句不超過 8 個字左右），語速放慢，避免片語動詞和俚語；如果我聽不懂或答不出來，用更簡單的說法重講一次，不用擔心囉嗦。',
  B1: '用常見、中等難度的單字和句型，可以用一些簡單的片語動詞，但避免生僻俚語；語速正常，句子可以有一點變化。',
  B2: '用自然道地的表達方式，可以包含片語動詞、慣用語和較複雜的句型，用接近母語者聊天的語速和方式跟我對話。',
}

export function buildConversationPrompt(params: {
  readonly episodeTitle: string
  readonly cues: readonly Cue[]
  readonly cefrLevel: CefrLevel
  readonly vocab: readonly { word: string; translation: string }[]
}): string {
  const { episodeTitle, cues, cefrLevel, vocab } = params
  const transcript = cues.map(cue => `${cue.speaker}: ${cue.text}`).join('\n')
  const vocabLine = vocab.length > 0
    ? `\n6. 這次我想特別練習使用這些單字：${vocab.map(v => v.word).join('、')}，請在對話中自然地帶到，或引導我自己說出來。`
    : ''

  return `你是我的英文口說教練，請根據以下這集 Podcast 的內容，和我進行一段「全英文」的口說對話練習。這是語音對話模式，不是文字聊天，請注意：

【對話規則】
1. 用「英文」跟我對話，每次只講 1-3 句，語氣自然口語化，不要長篇大論。
2. 每次只問「一個」開放式問題，問完就停下來等我開口回答，不要一次丟出好幾個問題。
3. 難度請對齊 CEFR ${cefrLevel} 等級：${LEVEL_GUIDANCE[cefrLevel]}
4. 如果我回答時有明顯的文法或用字錯誤，用自然、簡短的方式順勢帶過糾正（用你自己的話重講一次正確版本），不要打斷對話節奏長篇講解文法規則。
5. 話題請圍繞下面這集 Podcast 的內容展開，可以問我對內容的看法、要我用自己的話重述某段、或做延伸提問。${vocabLine}

【這集 Podcast 內容】
標題：${episodeTitle}
逐字稿：
"""
${transcript}
"""

現在請直接開始：先用一句話打招呼，再問我第一個問題。`
}
