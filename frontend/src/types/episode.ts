/** 單字在 cue 音檔內的時間戳（秒，相對於 cue.start，不是 episode-global）。 */
export type WordOffset = {
  readonly word: string
  readonly start: number
  readonly end: number
}

export type Cue = {
  readonly index: number
  readonly speaker: string
  readonly text: string
  readonly zh: string
  readonly start: number
  readonly end: number
  /** 詞級字幕：練習模式 word click 用。舊集 / edge-tts fallback 為 undefined。
   *  words[i].start 是相對於 cue.start 的秒數。 */
  readonly words?: readonly WordOffset[]
}

/** 集數外部來源連結（給播放器「參考資料」區塊使用）。 */
export type SourceReference = {
  readonly id: string
  readonly title: string
  readonly url: string
}

/** 單行 mp3 對前端契約：index + 已簽章 audioUrl + 真實時長 + 在該集的時間區段。
 *
 * 新方案下音檔以 segments 陣列承載，前端 Web Audio API 串接播；
 * audioUrl 對 episode 為 nullable 是為了向後相容舊 client（1 版本後移除）。
 */
export type Segment = {
  readonly index: number
  readonly audioUrl: string
  readonly duration: number
  readonly start: number
  readonly end: number
  /** 詞級字幕 JSON 簽章 URL：null 表示沒 word boundary（舊集 / edge fallback）。
   *  實務上前端 Cue.words 已內嵌 word 資料（後端 build_timeline 串進去），
   *  這欄位保留給未來 lazy-load word JSON 用，目前多數場景直接讀 Cue.words。 */
  readonly wordOffsetsUrl?: string | null
}

export type Episode = {
  readonly id: string
  readonly title: string
  /** 整集 mp3 簽章 URL。新方案下恆為 null；保留欄位給 1 版本向後相容。
   *  舊 client 仍讀此欄位時，後端會回 audio_r2_key 簽章好的 URL；Phase G 後
   *  此欄位完全移除，前端 consumer 必須讀 segments 並走 useSegmentPlayer hook。 */
  readonly audioUrl: string | null
  readonly segments: readonly Segment[]
  readonly cues: readonly Cue[]
  /** 資料來源／延伸閱讀；後端尚未合併 Episode 欄位時一律空陣列或 undefined，
   *  UI 端負責「無來源不渲染」。 */
  readonly references?: readonly SourceReference[]
}
