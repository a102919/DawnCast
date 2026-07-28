import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// Workbox runtime caching 政策常數（好品味：魔術數字命名，未來調 cache 集中改這裡）
const CACHE_MAX_ENTRIES = 100
const CACHE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30 // 30 天
const NAV_TIMEOUT_SECONDS = 3

// dev proxy：/api/* → localhost:8000
// 目的：從 devtunnels（https 公開來源）連本機 backend 時，
// 瀏覽器看到的 origin 是 same-origin 的 5173，繞過 Private Network Access (PNA) 政策。
// prod 不需要（部署時前端直連 API 網域）。
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      // 沿用既有手寫 manifest.json；plugin 不產生，避免覆蓋設計。
      manifest: false,
      // generateSW 用 Workbox runtime，自動 precache Vite hashed assets。
      strategies: 'generateSW',
      srcDir: 'public',
      filename: 'sw.js',
      // 'prompt' 模式：useRegisterSW hook 攔截 onNeedRefresh，
      // 由 React 元件決定何時 skipWaiting + reload，給使用者掌控權。
      registerType: 'prompt',
      injectRegister: false,
      devOptions: { enabled: false },
      workbox: {
        // 只 precache build 產物必要副檔名，避免 install 卡大檔。
        // push-sw.js 排除在 precache 外（它是 importScripts 進來的 SW 片段，
        // 不該被當成頁面資源快取），改由下面 importScripts 併入 sw.js。
        globPatterns: ['**/*.{js,css,html,ico,png,svg,webmanifest}'],
        globIgnores: ['**/push-sw.js'],
        // Web Push 的 push / notificationclick handler（見 public/push-sw.js）。
        // 維持 generateSW 策略：換 injectManifest 就得自己搬整套 runtimeCaching。
        importScripts: ['/push-sw.js'],
        // SPA fallback：未知路徑回 index.html
        navigateFallback: '/index.html',
        // OAuth callback、API 路由不交給 SW，避免快取含 code/state 的網址。
        navigateFallbackDenylist: [/^\/auth/, /^\/api/],
        cleanupOutdatedCaches: true,
        // 預設 skipWaiting: true 會強制 reload；改由 updateServiceWorker(true) 顯式觸發。
        skipWaiting: false,
        clientsClaim: true,
        runtimeCaching: [
          {
            urlPattern: ({ request }) =>
              ['style', 'script', 'worker', 'image', 'font', 'manifest'].includes(
                request.destination,
              ),
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'dawncast-assets',
              expiration: { maxEntries: CACHE_MAX_ENTRIES, maxAgeSeconds: CACHE_MAX_AGE_SECONDS },
            },
          },
          {
            urlPattern: ({ request }) => request.mode === 'navigate',
            handler: 'NetworkFirst',
            options: { cacheName: 'dawncast-pages', networkTimeoutSeconds: NAV_TIMEOUT_SECONDS },
          },
          // 每行 mp3 segment：R2 簽章 URL 帶 query string（每次請求新簽），用
          // CacheFirst 把已播過的 segment 離線快取，下次聽同集直接命中。
          // 不快取 .srt：srt 由 Episode.segments metadata 取代，整集 mp3 不再生產。
          {
            urlPattern: ({ url }) => url.pathname.endsWith('.mp3'),
            handler: 'CacheFirst',
            options: {
              cacheName: 'dawncast-segments',
              expiration: { maxEntries: 200, maxAgeSeconds: 7 * 24 * 60 * 60 },
              cacheableResponse: { statuses: [0, 200] },
              rangeRequests: true,
            },
          },
        ],
      },
    }),
  ],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      // 本機媒體 fallback：backend 回的 videoUrl 是相對 /media/{slug}.mp4，
      // vite 不 proxy 會落到 SPA history fallback 回 HTML；<video> 就壞了。
      '/media': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
