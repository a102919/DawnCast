import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/global.css'
import App from './App'
import { maybeDevBypass } from './lib/devBypass'

// devtunnels / 公開 URL 等無真實 OAuth 的場景：env 開啟後自動注入 fake session。
// 內部會 reload 一次，return true 表示「已觸發 reload、不該繼續 mount」。
if (maybeDevBypass()) {
  // 不 render，等 reload 後 supabase-js 重新 hydrate session
} else {
  const rootEl = document.getElementById('root')
  if (!rootEl) throw new Error('找不到 root element')

  createRoot(rootEl).render(
    <StrictMode>
      <App />
    </StrictMode>
  )
}
