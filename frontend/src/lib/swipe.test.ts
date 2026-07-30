import { describe, expect, it } from 'vitest'
import { decideSwipe } from './swipe'

describe('decideSwipe', () => {
  it('快甩：速度過門檻即 commit，方向由速度符號決定', () => {
    expect(decideSwipe(10, 600)).toBe('right')
    expect(decideSwipe(-10, -600)).toBe('left')
    // 位置在右但速度往左甩 → 依速度
    expect(decideSwipe(120, -600)).toBe('left')
  })

  it('慢拖：位置過門檻且速度不反向才 commit', () => {
    expect(decideSwipe(120, 0)).toBe('right')
    expect(decideSwipe(-120, -50)).toBe('left')
    // 拖過門檻但往回收 → 彈回
    expect(decideSwipe(120, -100)).toBeNull()
  })

  it('未達標：彈回', () => {
    expect(decideSwipe(50, 100)).toBeNull()
    expect(decideSwipe(-99, 0)).toBeNull()
  })
})
