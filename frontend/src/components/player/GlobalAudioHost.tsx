import { AudioPlayer } from './AudioPlayer'
import { usePlayer } from '../../state'

/** 常駐音檔節點：掛在 provider 樹上層，路由切換不會 unmount，離開播放頁後續播不中斷。
 * AudioPlayer 現在只是 hook consumer，不持有 DOM；實際 AudioContext 由 PlayerProvider 建。 */
export function GlobalAudioHost() {
  const { currentEpisode } = usePlayer()
  if (!currentEpisode) return null
  return <AudioPlayer />
}
