import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from '../lib/supabaseClient'
import { clearAdminToken } from '../api'
import { AuthContext, type AuthContextValue } from './authContextValue'

export function AuthProvider({ children }: { readonly children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  // 上一個登入的 user id：偵測「換了不同帳號」事件（onAuthStateChange 沒給明確 event，
  // 得自前後比對），共用裝置切換帳號時清掉前一位留下的 X-Admin-Token。
  const prevUserIdRef = useRef<string | null>(null)

  useEffect(() => {
    let active = true

    const init = async () => {
      const { data } = await supabase.auth.getSession()
      if (!active) return
      setSession(data.session)
      prevUserIdRef.current = data.session?.user?.id ?? null
      setIsLoading(false)
    }
    void init()

    const { data: sub } = supabase.auth.onAuthStateChange((event, next) => {
      setSession(next)
      setIsLoading(false)
      // session 失效（TOKEN_REFRESHED 失敗自動 SIGNED_OUT）或換了不同帳號時，
      // 同步清掉 legacy X-Admin-Token——後端 require_admin 對 X-Admin-Token
      // 與 Supabase JWT email 白名單採 OR 語意，否則共用裝置下一位使用者會沿用
      // 上一位留下的 token，繞過 Gmail 白名單。
      if (event === 'SIGNED_OUT' || (next?.user?.id && prevUserIdRef.current && next.user.id !== prevUserIdRef.current)) {
        clearAdminToken()
      }
      prevUserIdRef.current = next?.user?.id ?? null
    })

    return () => {
      active = false
      sub.subscription.unsubscribe()
    }
  }, [])

  const signInWithGoogle = useCallback(async () => {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: { redirectTo: window.location.origin },
    })
    if (error) throw error
  }, [])

  const signOut = useCallback(async () => {
    const { error } = await supabase.auth.signOut()
    if (error) throw error
    if ('caches' in window) {
      const keys = await caches.keys()
      await Promise.all(keys.map((key) => caches.delete(key)))
    }
    // 共用裝置登出：清掉 admin token 與所有 dawncast: 前綴的本機狀態（播放進度、
    // 活動紀錄等），避免同一台裝置下一位使用者沿用上一位的殘留狀態。
    clearAdminToken()
    const keysToRemove: string[] = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && key.startsWith('dawncast:')) keysToRemove.push(key)
    }
    for (const key of keysToRemove) localStorage.removeItem(key)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      user: session?.user ?? null,
      isLoading,
      signInWithGoogle,
      signOut,
    }),
    [session, isLoading, signInWithGoogle, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
