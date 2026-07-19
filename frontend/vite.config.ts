import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// dev proxy：/api/* → localhost:8000
// 目的：從 devtunnels（https 公開來源）連本機 backend 時，
// 瀏覽器看到的 origin 是 same-origin 的 5173，繞過 Private Network Access (PNA) 政策。
// prod 不需要（部署時前端直連 API 網域）。
export default defineConfig({
  plugins: [react(), tailwindcss()],
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
