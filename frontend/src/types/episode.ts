export type Cue = {
  readonly index: number
  readonly speaker: string
  readonly text: string
  readonly zh: string
  readonly start: number
  readonly end: number
}

/** 集數外部來源連結（給玩家「資料來源／延伸閱讀」區塊使用）。
 *  - title：連結顯示文字（繁中）
 *  - url：外部連結（必填，UI 一律 target="_blank" rel="noopener noreferrer"）
 *  - publisher：發布單位／網域名稱（選填，顯示在標題後面當小字）
 */
export type SourceReference = {
  readonly title: string
  readonly url: string
  readonly publisher?: string | null
}

export type Episode = {
  readonly id: string
  readonly title: string
  readonly audioUrl: string
  readonly cues: readonly Cue[]
  /** 資料來源／延伸閱讀；後端尚未合併 Episode 欄位時一律空陣列或 undefined，
   *  UI 端負責「無來源不渲染」。 */
  readonly references?: readonly SourceReference[]
}
