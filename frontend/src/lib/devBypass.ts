/**
 * Dev bypass：自動注入 fake Supabase session，讓 devtunnels 公開 URL 等
 * 「沒有真實 OAuth」的情境下，前端能以 DEV_USER 身份打後端 API。
 *
 * 觸發條件：VITE_DEV_AUTH_BYPASS=true 且 localStorage 沒有 supabase session。
 * 行為：寫入 fake session JSON 後 reload 一次（讓 supabase-js 重新 hydrate）。
 *
 * 後端對接：backend/app/deps.py 的 get_current_user 在 dev mode + DEV_AUTH_BYPASS=true
 * 時，「Authorization: Bearer dev」會直接回 DEV_USER_ID。所以 token 寫 `dev` 就夠。
 *
 * 預設關閉，prod 環境設 false（或不設）即完全 no-op。
 */

const SUPABASE_SESSION_KEY_PREFIX = 'sb-'

function getEnabled(): boolean {
  return import.meta.env.VITE_DEV_AUTH_BYPASS === 'true'
}

function getSupabaseRef(): string | null {
  const url = import.meta.env.VITE_SUPABASE_URL ?? ''
  // https://<ref>.supabase.co → 取 <ref>
  const match = url.match(/^https:\/\/([^.]+)\.supabase\.co\/?$/)
  return match ? match[1] : null
}

function hasSupabaseSession(): boolean {
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key && key.startsWith(SUPABASE_SESSION_KEY_PREFIX) && key.endsWith('-auth-token')) {
      return true
    }
  }
  return false
}

/** 注入 fake session 並 reload 一次。已登入或關閉時 no-op。回傳是否執行了 reload。 */
export function maybeDevBypass(): boolean {
  if (!getEnabled()) return false
  if (typeof window === 'undefined') return false
  if (hasSupabaseSession()) return false

  const ref = getSupabaseRef()
  if (!ref) return false

  const key = `${SUPABASE_SESSION_KEY_PREFIX}${ref}-auth-token`
  const fakeSession = {
    access_token: 'dev',
    token_type: 'bearer',
    expires_in: 3600,
    // 一年後才過期，避免 autoRefresh 觸發打 supabase
    expires_at: Math.floor(Date.now() / 1000) + 3600 * 24 * 365,
    refresh_token: 'fake-refresh',
    user: {
      id: '00000000-0000-0000-0000-000000000001',
      email: 'dev@local',
      aud: 'authenticated',
      role: 'authenticated',
    },
  }
  localStorage.setItem(key, JSON.stringify(fakeSession))
  // 整頁 reload 才能讓 supabase-js 重新走 init flow、把 session 餵進內部狀態
  window.location.reload()
  return true
}
