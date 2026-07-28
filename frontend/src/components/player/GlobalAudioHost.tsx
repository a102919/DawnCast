/** 常駐音檔節點：實際 AudioContext 由 PlayerProvider 建、播放邏輯在 useSegmentPlayer，
 * 這裡不再持有任何 DOM 或 hook consumer，純粹保留掛載點供未來擴充。 */
export function GlobalAudioHost() {
  return null
}
