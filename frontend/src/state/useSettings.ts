import { useContext } from 'react'
import { SettingsContext, type SettingsContextValue } from './settingsContextValue'

export function useSettings(): SettingsContextValue {
  const ctx = useContext(SettingsContext)
  if (!ctx) throw new Error('useSettings must be used inside SettingsProvider')
  return ctx
}
