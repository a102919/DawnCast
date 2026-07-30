import type { ReactNode } from 'react'

/** 學習/複習/測驗 session 頁的單屏容器：沉浸式（無 TopBar/BottomNav）下吃滿
 *  dvh，內容用 flex 直排——卡片區 flex-1、按鈕固定底部，整頁不捲動。 */
export function SessionShell({ children }: { readonly children: ReactNode }) {
  return (
    <div className="flex flex-col h-dvh w-full max-w-2xl mx-auto px-4 pt-[max(0.75rem,env(safe-area-inset-top))] pb-[max(0.75rem,env(safe-area-inset-bottom))] overflow-x-hidden">
      {children}
    </div>
  )
}
