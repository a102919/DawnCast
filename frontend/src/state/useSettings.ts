import { createContextHook } from './createContextHook'
import { SettingsContext, type SettingsContextValue } from './settingsContextValue'

export const useSettings: () => SettingsContextValue = createContextHook(
  SettingsContext,
  'useSettings',
  'SettingsProvider',
)
