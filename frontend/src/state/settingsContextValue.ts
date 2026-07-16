import { createContext } from 'react'
import type { Settings } from '../api'

export type SettingsContextValue = {
  readonly settings: Settings
  updateSettings(patch: Partial<Settings>): Promise<void>
  resetPopupPreferences(): Promise<void>
}

export const SettingsContext = createContext<SettingsContextValue | null>(null)
