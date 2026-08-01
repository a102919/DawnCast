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
 * 過渡期契約：後端已改產整集單一 mp3（Episode.audioUrl），前端播放不再讀這個
 * 欄位；後端仍雙寫一段時間讓舊 client 相容，下一版停產後這個型別會跟著刪除。
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
  /** 整集 mp3 簽章 URL：句間停頓已烤進音檔，時間軸與音檔零誤差。null 表示這集
   *  還沒有可播的整集音檔（舊集尚未 backfill、或後端產生失敗），
   *  useSegmentPlayer.loadEpisode 會轉成 error state。 */
  readonly audioUrl: string | null
  readonly segments: readonly Segment[]
  readonly cues: readonly Cue[]
  /** 資料來源／延伸閱讀；後端尚未合併 Episode 欄位時一律空陣列或 undefined，
   *  UI 端負責「無來源不渲染」。 */
  readonly references?: readonly SourceReference[]
}
