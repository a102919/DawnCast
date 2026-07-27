// Web Push 的 service worker 片段：由 vite.config.ts 的 workbox.importScripts
// 併入 generateSW 產出的 sw.js（不換 injectManifest 策略，避免把整套 runtimeCaching
// 設定搬進手寫 SW）。
//
// payload 由 backend/shared/push.py 送出：{ title, body, url }。

self.addEventListener('push', event => {
  if (!event.data) return

  let payload = {}
  try {
    payload = event.data.json()
  } catch {
    // 非 JSON payload（理論上不會發生）：顯示純文字，總比靜默吞掉好。
    payload = { body: event.data.text() }
  }

  const title = payload.title || 'DawnCast'
  event.waitUntil(
    self.registration.showNotification(title, {
      body: payload.body || '',
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      // tag 讓同類通知覆蓋而非疊成一長串（每日通知一天只該有一則可見）。
      tag: payload.url || '/',
      data: { url: payload.url || '/' },
    }),
  )
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  const target = new URL(event.notification.data?.url || '/', self.location.origin).href

  event.waitUntil(
    (async () => {
      const clientList = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      // 已經開著的分頁優先 focus，不要每次點通知都開新視窗。
      for (const client of clientList) {
        if (client.url === target) return client.focus()
      }
      const existing = clientList[0]
      if (existing && 'navigate' in existing) {
        await existing.focus()
        return existing.navigate(target)
      }
      return self.clients.openWindow(target)
    })(),
  )
})
