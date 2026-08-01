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
          // 不快取 .mp3：Safari 對 <audio src> 預設發 byte-range 預讀
          // （Range: bytes=0-1），CacheFirst 把 206 partial 存進 cache 後，
          // 後續 Safari 再發 Range byte N- 時，Workbox RangeRequestsPlugin 用
          // partial 算 range 對不上 → fallback 自製 416 + text/plain body →
          // Safari 視為「URL 不支援播放」→ NotSupportedError（永久）。
          // 另：podcast 隨時可重新 stream，1-5 MB 整檔 fetch 即可，無離線需求。
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
      // 本機 R2 mock：backend segment mp3 完整 URL 是 http://localhost:8000/mock-r2/...，
      // 前端 <audio src> 直連會被 Chromium 視為跨原始拒絕。
      // 走 vite proxy 變 same-origin，CORS / PNA 都跳過。
      '/mock-r2': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
