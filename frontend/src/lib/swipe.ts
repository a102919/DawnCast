export type SwipeDirection = 'left' | 'right'

/** AnimatePresence custom：exit 需要方向與釋放速度才能無縫接手飛出動畫。 */
export type SwipeExit = {
  readonly dir: SwipeDirection
  readonly velocity: number
  readonly reduce: boolean
}

/** 滑卡 commit 判定（與 Sheet.tsx drag-to-dismiss 同手感）：速度優先於位置，
 *  方向由速度符號決定；慢拖過門檻時速度不得反向。未達標回傳 null（彈回）。 */
export function decideSwipe(offsetX: number, velocityX: number): SwipeDirection | null {
  if (Math.abs(velocityX) > 500) return velocityX > 0 ? 'right' : 'left'
  if (Math.abs(offsetX) > 100) {
    const dir: SwipeDirection = offsetX > 0 ? 'right' : 'left'
    if (dir === 'right' ? velocityX >= 0 : velocityX <= 0) return dir
  }
  return null
}
