import { Play } from 'lucide-react'
import { usePlayer } from '../../state'
import { api } from '../../api'
import { AppError } from '../../api/httpApi'
import { findActiveCueIndex } from '../../lib/time'

interface ReplayAudioButtonProps {
  readonly episodeSlug: string
  /** 該單字在源集 cue 內的「全球播放位置」（秒） */
  readonly timestamp: number
}

/** 從 PlayerProvider 的 useSegmentPlayer 抽樣播：player 知道現在載入的 episode、
 *  decoded segments，主音 ducking 後播這一段，保證跟 cue 對齊。
 * 不再走獨立的 new Audio() + audioUrlCache，因為：
 * - 整集 mp3 已不生產（Phase A 之後），原 url 端點 deprecated。
 * - per-segment AudioBuffer 已是 truth source，從 source.start 抽樣等於「從
 *   該行的真實音檔」播，發音/語氣跟主集完全一致。 */
export function ReplayAudioButton({ episodeSlug, timestamp }: ReplayAudioButtonProps) {
  const player = usePlayer()
  const playSegment = player.playSegment
  const currentEpisode = player.currentEpisode

  // 重播時若 player 還沒載到該 episode → 走 api.getEpisode 補抓並 setCurrentEpisode，
  // 然後 wait loadState 變 ready 再 playSegment。
  const handleClick = async () => {
    try {
      let ep = currentEpisode
      if (!ep || ep.id !== episodeSlug) {
        ep = await api.getEpisode(episodeSlug)
        player.setCurrentEpisode(ep)
      }
      if (!ep) return
      const cues = ep.cues
      // timestamp 落在第一個 cue 之前時 findActiveCueIndex 回 -1，退回第 0 個
      // cue，保持跟原本 binary search 版本一致的「找不到就播開頭」行為。
      const idx = Math.max(0, findActiveCueIndex(cues, timestamp))
      const cue = cues[idx]
      if (!cue) return
      const offsetSec = Math.max(0, Math.min(timestamp - cue.start, cue.end - cue.start))
      playSegment(idx, offsetSec, 0.6)
    } catch (e) {
      if (e instanceof AppError) {
        console.warn('[ReplayAudioButton] 載入失敗', e.message)
      }
    }
  }

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-bg-secondary text-text-secondary hover:text-accent hover:bg-accent/10 transition-colors duration-fast"
    >
      <Play size={13} />
      重播原音
    </button>
  )
}
