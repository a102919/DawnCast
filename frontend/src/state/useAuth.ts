import { createContextHook } from './createContextHook'
import { AuthContext, type AuthContextValue } from './authContextValue'

export const useAuth: () => AuthContextValue = createContextHook(
  AuthContext,
  'useAuth',
  'AuthProvider',
)
