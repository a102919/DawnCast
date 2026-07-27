import { useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { useRegisterSW } from 'virtual:pwa-register/react'

// PWA 更新提示：新 SW 進入 waiting 狀態時顯示「重新整理」toast，
// 點擊後 updateServiceWorker(true) 觸發 skipWaiting + reload。
// 離線就緒時短暫提示一次。
// 必須掛在 Toaster 已 mount 的位置（與 App 同一棵樹）。
export function UpdatePrompt() {
  const updateServiceWorkerRef = useRef<((reloadPage?: boolean) => Promise<void>) | undefined>(undefined)
  const {
    needRefresh: [needRefresh],
    offlineReady: [offlineReady],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisterError(error) {
      console.error('[PWA] SW register failed', error)
    },
  })

  // ref 必須在 effect 內更新；render 期更新 ref 違反 react-hooks/refs 規則。
  useEffect(() => {
    updateServiceWorkerRef.current = updateServiceWorker
  }, [updateServiceWorker])

  useEffect(() => {
    if (!needRefresh) return
    const id = toast('有新版本可用', {
      description: '點擊「重新整理」套用新版本',
      duration: Infinity,
      action: {
        label: '重新整理',
        onClick: () => {
          void updateServiceWorkerRef.current?.(true)
        },
      },
    })
    return () => {
      toast.dismiss(id)
    }
  }, [needRefresh])

  useEffect(() => {
    if (!offlineReady) return
    const id = toast('已可離線使用', {
      description: '目前內容已可離線瀏覽',
      duration: 5000,
    })
    return () => {
      toast.dismiss(id)
    }
  }, [offlineReady])

  return null
}
