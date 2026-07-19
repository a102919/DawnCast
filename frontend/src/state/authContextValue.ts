import { createContext } from 'react'
import type { Session, User } from '@supabase/supabase-js'

export type AuthContextValue = {
  readonly session: Session | null
  readonly user: User | null
  /** session 是否還在初始化（首次讀取 getSession） */
  readonly isLoading: boolean
  /** 導去 Google OAuth 同意畫面 */
  signInWithGoogle(): Promise<void>
  signOut(): Promise<void>
}

export const AuthContext = createContext<AuthContextValue | null>(null)
