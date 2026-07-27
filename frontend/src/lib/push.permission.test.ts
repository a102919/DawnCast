// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getNotificationPermission } from './push'

describe('getNotificationPermission', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    // 還原 happy-dom 預設的 Notification，避免下一個 describe 互相污染
    ;(window as unknown as { Notification?: unknown }).Notification = undefined
    ;(globalThis as unknown as { Notification?: unknown }).Notification = undefined
  })

  it('回傳 Notification.permission 當前值（granted / default / denied）', () => {
    for (const value of ['granted', 'default', 'denied'] as const) {
      vi.stubGlobal('Notification', { permission: value })
      expect(getNotificationPermission()).toBe(value)
    }
  })

  it('Notification 不存在時回 unsupported（iOS Safari 沒加主畫面等情境）', () => {
    // happy-dom 預設就有 Notification 全域，要明確刪掉才能走到「in window === false」分支
    delete (window as unknown as { Notification?: unknown }).Notification
    delete (globalThis as unknown as { Notification?: unknown }).Notification
    expect(getNotificationPermission()).toBe('unsupported')
  })
})
