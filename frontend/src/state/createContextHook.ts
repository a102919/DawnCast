import { useContext, type Context } from 'react'

/** 9 個 useXxx hook 都是同一套「useContext + null 檢查拋錯」樣板，這裡收斂成一處。 */
export function createContextHook<T>(ctx: Context<T | null>, hookName: string, providerName: string): () => T {
  return function useContextValue(): T {
    const value = useContext(ctx)
    if (!value) throw new Error(`${hookName} must be used inside ${providerName}`)
    return value
  }
}
