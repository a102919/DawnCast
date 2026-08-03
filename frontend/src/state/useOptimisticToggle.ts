import { useCallback, useRef } from 'react'

/** 每個 key 同一時間只送一個請求；連點時只更新這裡記錄的「最新想要的狀態」，
 *  讓飛行中的請求結束後自己判斷要不要再送一次，而不是讓兩個請求互相競速。
 *  收斂 FavoritesProvider / ChannelSubscriptionsProvider 原本重複的樂觀更新
 *  retry 迴圈。 */
export function useOptimisticToggle<K>(label: string) {
  const pendingRef = useRef<Map<K, boolean>>(new Map())

  return useCallback(
    async (key: K, desiredInitial: boolean, sync: (desired: boolean) => Promise<void>, onRevert: (desired: boolean) => void) => {
      const hasInFlight = pendingRef.current.has(key)
      pendingRef.current.set(key, desiredInitial)
      if (hasInFlight) return

      let desired = desiredInitial
      for (;;) {
        try {
          await sync(desired)
        } catch (err) {
          console.warn(`[${label}] toggle sync failed`, err)
          onRevert(desired)
          break
        }
        const latest = pendingRef.current.get(key)
        if (latest === undefined || latest === desired) break
        desired = latest
      }
      pendingRef.current.delete(key)
    },
    [label],
  )
}
