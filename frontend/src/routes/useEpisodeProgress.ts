import { useEffect, useRef } from 'react'
import type { DailyOrder } from '../api'
import type { SegmentLoadState } from '../state/useSegmentPlayer'
import type { Episode } from '../types/episode'

export interface UseEpisodeProgressParams {
  readonly episode: Episode | null
  readonly currentTime: number
  readonly duration: number
  readonly loadState: SegmentLoadState
  /** 全域 PlayerProvider 目前正在播的集數；用來判斷是不是「冷啟動」續播。 */
  readonly currentEpisode: Episode | null
  /** 這集是不是由點餐訂單（?orderId=）解析出來的；沒有就不會呼叫 markPlayed，
   *  避免任意集數播放都誤觸發「這張訂單已播放」（見 useEpisode.ts）。 */
  readonly orderId: string | null
  /** true 時代表這次進頁帶著明確跳轉目標（單字本「跳到」／「前往該集」），
   *  跳過續播定位，避免兩個 seekTo 打架造成使用者聽到雙重跳動。 */
  readonly skipResumeSeek?: boolean
  seekTo(time: number): void
  loadProgress(episodeId: string): { readonly currentTime: number; readonly exists: boolean }
  markListened(episodeId: string): void
  addListenMinutes(month: string, minutes: number): void
  markPlayed(orderId: string): Promise<DailyOrder | null>
  recordPlay(episodeId: string): Promise<void>
}

/** 續播定位 + 80%/90% 完聽節流標記 + 播放次數回報。
 *
 * 四件事都以「同一集只做一次」為前提，靠 ref（而非 state）記帳，避免額外 re-render。 */
export function useEpisodeProgress({
  episode, currentTime, duration, loadState, currentEpisode, orderId, skipResumeSeek,
  seekTo, loadProgress, markListened, addListenMinutes, markPlayed, recordPlay,
}: UseEpisodeProgressParams): void {
  const episodeIdRef = useRef<string | null>(null)
  const initialSeekAppliedRef = useRef(false)
  const hasMarkedListenedRef = useRef(false)
  const hasMarkedDailyPlayedRef = useRef(false)
  const hasRecordedPlayRef = useRef(false)

  useEffect(() => {
    if (episode && episode.id !== episodeIdRef.current) {
      episodeIdRef.current = episode.id
      initialSeekAppliedRef.current = false
      hasMarkedListenedRef.current = false
      hasRecordedPlayRef.current = false
    }
  }, [episode])

  useEffect(() => {
    if (!episode || initialSeekAppliedRef.current) return
    // skipResumeSeek 只在「剛帶跳轉目標進頁」那一輪 render 是 true；跳轉目標消化完
    // 會清掉 router state，下一輪 render 這裡又會是 false。必須把「這集不用續播定位」
    // 就地記到 ref（跟正常續播共用同一顆 initialSeekAppliedRef），不然清 state 後
    // 這個 effect 會誤判成「還沒定位過」，把單字本跳轉的位置蓋成 localStorage 的舊進度。
    if (skipResumeSeek) {
      initialSeekAppliedRef.current = true
      return
    }
    const episodeId = episode.id
    // 全域 PlayerProvider 已在播這集（例：從 MiniPlayer 點回播放頁）→ currentTime
    // 才是事實。localStorage 進度是節流快照，會落後幾百毫秒到 1 秒，
    // 拿它去 seek 就是使用者看到的「點進來倒退一下」。只有冷啟動才需要續播定位。
    if (currentEpisode?.id === episodeId && currentTime > 0) {
      initialSeekAppliedRef.current = true
      loadProgress(episodeId) // 副作用：綁定 provider 的 currentEpisodeIdRef，續存進度
      return
    }
    const progress = loadProgress(episodeId)
    if (!progress.exists) return

    // 等 hook loadState === 'ready'（segments decode 完成）才能 seek，否則會定位到 0。
    if (loadState !== 'ready') return
    initialSeekAppliedRef.current = true
    seekTo(progress.currentTime)
  }, [episode, currentEpisode, currentTime, loadProgress, loadState, seekTo, skipResumeSeek])

  useEffect(() => {
    if (!episode || duration <= 0 || hasMarkedListenedRef.current) return
    if (currentTime / duration > 0.8) {
      hasMarkedListenedRef.current = true
      markListened(episode.id)
      const ymMin = new Date().toLocaleDateString('en-CA').slice(0, 7)
      addListenMinutes(ymMin, Math.floor(currentTime / 60))
    }
  }, [currentTime, duration, episode, markListened, addListenMinutes])

  useEffect(() => {
    if (!episode || duration <= 0 || hasMarkedDailyPlayedRef.current) return
    // orderId 沒有值＝這集不是點餐訂單交付的那集（例如直接聽頻道/歷史集數），
    // 不屬於任何「進行中訂單」，不觸發 markPlayed（修正舊版用「今天日期」
    // 硬編碼、任何集數播完都誤標記今日訂單已播放的 bug）。
    if (!orderId) return
    if (currentTime / duration >= 0.9) {
      hasMarkedDailyPlayedRef.current = true
      // fire-and-forget：失敗不影響播放，靜默吞掉避免未捕捉 rejection。
      void markPlayed(orderId).catch(() => undefined)
    }
  }, [currentTime, duration, episode, markPlayed, orderId])

  useEffect(() => {
    // 播滿 5 秒才算一次播放：擋掉誤觸與快速滑過，不然數字會被雜訊灌水。
    if (!episode || hasRecordedPlayRef.current || currentTime < 5) return
    hasRecordedPlayRef.current = true
    // fire-and-forget：失敗不影響播放，靜默吞掉避免未捕捉 rejection。
    void recordPlay(episode.id).catch(() => undefined)
  }, [currentTime, episode, recordPlay])
}
