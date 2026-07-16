import { createClient, type SupabaseClient } from '@supabase/supabase-js'

// 僅用於 Auth：拿 magic link session / JWT。資料一律走後端 REST API。
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL ?? ''
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY ?? ''

// mock 模式（VITE_USE_MOCK !== 'false'）可不設這兩個變數；此時 client 仍可建立，
// 只是不會真的呼叫 Supabase。實際登入流程只在 http 模式下觸發。
export const supabase: SupabaseClient = createClient(
  SUPABASE_URL || 'https://placeholder.supabase.co',
  SUPABASE_ANON_KEY || 'placeholder-anon-key',
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  },
)

/** 取目前 session 的 access token（JWT），未登入回 null。 */
export async function getAccessToken(): Promise<string | null> {
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}
