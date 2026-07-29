/** 主播放意圖狀態機：純函式、零 DOM、零非同步。
 *
 * 只接收「已確定還算數」的 transition——跨 await 的過期判斷（要不要 dispatch 這個
 * action）全部在 useSegmentPlayer.ts 的 guard() 那層處理掉，reducer 完全不碰
 * 取消/世代比對這類語意，才能保持 100% 同步、可窮舉測試。
 *
 * pendingPlay（loading 中按播放要排隊）刻意不放進這裡，維持呼叫端的 plain ref——
 * 這是會被「另一個 async 續體、跨 render 呼叫」讀取的資料，放進 reducer state
 * 一樣會重演 stale closure 問題，只是換了個容器名字。
 */

import type { SegmentPlayer } from './useSegmentPlayer'
import type { Segment } from '../types/episode'

export type MainPlayerState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'loading' }
  | { readonly kind: 'error' }
  | { readonly kind: 'paused' }
  | { readonly kind: 'playing' }

export type MainPlayerAction =
  | { readonly type: 'LOAD_STARTED' }
  | { readonly type: 'LOAD_SUCCEEDED' }
  | { readonly type: 'LOAD_FAILED' }
  | { readonly type: 'LOAD_CLEARED' }
  | { readonly type: 'PLAYBACK_STARTED' }
  | { readonly type: 'PLAYBACK_STOPPED' }

export const initialMainPlayerState: MainPlayerState = { kind: 'idle' }

/** 對「這個 action 在目前 state 底下不合法」一律回傳原 state（no-op），不丟例外——
 *  正常業務流程本來就不該打出這種組合（例如 idle 收到 PLAYBACK_STARTED），但 reducer
 *  身為獨立可測的安全網，遇到不合法組合要能被 fuzz 測試證明「不會爆炸」，不是「不會發生」。 */
export function playerReducer(state: MainPlayerState, action: MainPlayerAction): MainPlayerState {
  switch (action.type) {
    case 'LOAD_STARTED':
      return { kind: 'loading' }
    case 'LOAD_SUCCEEDED':
      return state.kind === 'loading' ? { kind: 'paused' } : state
    case 'LOAD_FAILED':
      return state.kind === 'loading' ? { kind: 'error' } : state
    case 'LOAD_CLEARED':
      return { kind: 'idle' }
    case 'PLAYBACK_STARTED':
      return state.kind === 'paused' || state.kind === 'playing' ? { kind: 'playing' } : state
    case 'PLAYBACK_STOPPED':
      return state.kind === 'playing' || state.kind === 'paused' ? { kind: 'paused' } : state
  }
}

/** 唯一一處把 reducer 內部 state 轉成公開介面欄位；exhaustive switch，
 *  漏了任何一個 kind，tsc 會在這裡報錯（函式要求回傳值）。 */
export function toPublicFields(state: MainPlayerState): Pick<SegmentPlayer, 'loadState' | 'isPlaying'> {
  switch (state.kind) {
    case 'idle': return { loadState: 'idle', isPlaying: false }
    case 'loading': return { loadState: 'loading', isPlaying: false }
    case 'error': return { loadState: 'error', isPlaying: false }
    case 'paused': return { loadState: 'ready', isPlaying: false }
    case 'playing': return { loadState: 'ready', isPlaying: true }
  }
}

/** seekTo 用的 binary search：找出 globalSec 落在哪個 segment。segments 依 start 遞增排序。 */
export function findSegmentForTime(segments: readonly Segment[], globalSec: number): number {
  let lo = 0, hi = segments.length - 1, idx = 0
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const seg = segments[mid]
    if (!seg) break
    if (globalSec < seg.start) hi = mid - 1
    else if (globalSec > seg.end) { idx = mid; lo = mid + 1 }
    else { idx = mid; break }
  }
  return idx
}

/** 全域秒數換算成落在該 segment 內的 offset，夾在 [0, seg.duration] 之間。 */
export function clampOffset(globalSec: number, seg: Segment): number {
  return Math.max(0, Math.min(globalSec - seg.start, seg.duration))
}
