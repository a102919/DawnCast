export type Cue = {
  readonly index: number
  readonly speaker: string
  readonly text: string
  readonly zh: string
  readonly start: number
  readonly end: number
}

/** 集數外部來源連結（給播放器「參考資料」區塊使用）。 */
export type SourceReference = {
  readonly id: string
  readonly title: string
  readonly url: string
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
