// 與 vite.config.ts 分離：vite 8 用 rolldown、vitest 內建 vite 用 rollup，
// defineConfig 來源不同會報 Plugin 型別不相容；讓 vitest 走自己的設定即可。
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // happy-dom 提供 DOM 給 React 元件測試；純函式測試（httpApi）透過檔案內
    // // @vitest-environment node 註解個別覆寫，沿用預設 node 即可。
    environment: 'happy-dom',
  },
})