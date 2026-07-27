// 首次進入主動詢問推播權限的頂部通知列。
//
// 三個 AND gate 決定要不要彈：
//   1. 瀏覽器支援（iOS Safari 沒加主畫面 → false，不彈）
//   2. Notification.permission === 'default'（瀏覽器還沒決定過；denied 不彈避免騷擾）
//   3. localStorage 沒有 dawncast:push:dismissed 標記
//
// 通過 gate 後延遲 1.5 秒才彈，避免跟首頁 LCP 搶資源；讓首屏先 render 完。
// 「稍後」寫 localStorage（不再彈），「開啟」呼叫既有 enablePush() 走瀏覽器 prompt。
// Settings 頁 toggle 仍是 fallback 入口，dismiss 之後使用者還能從設定開。

import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Sheet } from './primitives/Sheet'
import {
  enablePush,
  getNotificationPermission,
  isPushSupported,
} from '../lib'
import { storageGet, storageSet } from '../lib/storage'

const DISMISS_KEY = 'dawncast:push:dismissed'
const SHOW_DELAY_MS = 1500

export function PushPrompt() {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (!isPushSupported()) return
    if (getNotificationPermission() !== 'default') return
    if (storageGet<boolean>(DISMISS_KEY)) return

    const timer = window.setTimeout(() => setOpen(true), SHOW_DELAY_MS)
    return () => window.clearTimeout(timer)
  }, [])

  const handleAllow = async () => {
    try {
      await enablePush()
      setOpen(false)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : '推播設定失敗，請稍後再試')
      setOpen(false)
    }
  }

  const handleDismiss = () => {
    storageSet(DISMISS_KEY, true)
    setOpen(false)
  }

  return (
    <Sheet
      isOpen={open}
      onClose={handleDismiss}
      variant="top"
      ariaLabelledBy="push-prompt-title"
    >
      <div className="px-6 pt-4 pb-6 flex flex-col gap-4">
        <h2
          id="push-prompt-title"
          className="text-xl font-semibold text-text-primary tracking-tight"
        >
          開啟推播通知
        </h2>
        <p className="text-[15px] leading-relaxed text-text-secondary">
          你的 podcast 一出爐就推給你，第一時間聽到新內容；不打擾，隨時能在設定關閉。
        </p>
        <div className="flex gap-3 pt-1">
          <button
            type="button"
            onClick={handleAllow}
            className="flex-1 rounded-xl py-3 font-medium text-white bg-accent hover:bg-accent-hover active:scale-[0.97] transition-[background-color,transform] duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
          >
            開啟推播
          </button>
          <button
            type="button"
            onClick={handleDismiss}
            className="flex-1 rounded-xl py-3 font-medium text-text-secondary bg-bg-secondary hover:bg-border active:scale-[0.97] transition-[background-color,transform] duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2"
          >
            稍後再說
          </button>
        </div>
      </div>
    </Sheet>
  )
}
