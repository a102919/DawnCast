import { Play } from 'lucide-react'
import { usePlayer } from '../../state'
import { api } from '../../api'
import { AppError } from '../../api/httpApi'
import { findActiveCueIndex } from '../../lib/time'

interface ReplayAudioButtonProps {
  readonly episodeSlug: string
  /** 該單字在源集 cue 內的「全球播放位置」（秒） */
  readonly timestamp: number
  /** 收錄當下的 cue 陣列索引（sourceLineNo）。timestamp 經 DB float4 捨入可能比
   *  cue.start 小一點點，用時間戳反查會掉到前一句；有精確索引就優先用。 */
  readonly lineNo?: number
}

/** 從 PlayerProvider 的 useSegmentPlayer 抽樣播：player 知道現在載入的 episode，
 *  用試聽元素從整集音檔的對應時間點播 0.6 秒，保證跟 cue 對齊、發音跟主集完全一致。 */
export function ReplayAudioButton({ episodeSlug, timestamp, lineNo }: ReplayAudioButtonProps) {
  const player = usePlayer()
  const playClip = player.playClip
  const currentEpisode = player.currentEpisode

  // 重播時若 player 還沒載到該 episode → 走 api.getEpisode 補抓並 setCurrentEpisode，
  // 再算出該行 cue 對應的整集時間點 playClip。
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
      const idx = lineNo !== undefined && lineNo >= 0 && lineNo < cues.length
        ? lineNo
        : Math.max(0, findActiveCueIndex(cues, timestamp))
      const cue = cues[idx]
      if (!cue) return
      const offsetSec = Math.max(0, Math.min(timestamp - cue.start, cue.end - cue.start))
      playClip(cue.start + offsetSec, 0.6)
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
