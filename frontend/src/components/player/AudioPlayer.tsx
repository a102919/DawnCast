/** Audio-only player：無 <audio> 元素，改用 useSegmentPlayer hook。
 *
 * AudioPlayer 純粹負責 iOS Safari gesture unlock 入口（首次 play() 必須在 click
 * handler 同步路徑內 ctx.resume() 才能解鎖發聲）；實際 AudioContext 與 buffer
 * cache 在 PlayerProvider → useSegmentPlayer 內。
 * PlayerRoute / GlobalAudioHost 透過 Provider play() 觸發播放，AudioPlayer 不再
 * 持有 ref 或 DOM。
 */
import { usePlayer } from '../../state'

export function AudioPlayer() {
  // mount 時不做事：PlayerProvider 已建好 AudioContext；首次播放 unlock 由
  // PlayerControls / LyricsView 的 play button 在 click handler 內觸發。
  usePlayer()
  return null
}
