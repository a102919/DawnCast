import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, type Settings } from '../api'
import { SettingsContext, type SettingsContextValue } from './settingsContextValue'

const DEFAULT_SETTINGS: Settings = {
  popupEnabled: true,
  playbackRate: 1,
  theme: 'auto',
  preferredTopics: [],
  defaultDeliveryTime: '07:00',
  cefrLevel: 'B1',
} as const

export function SettingsProvider({ children }: { readonly children: ReactNode }) {
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS)

  useEffect(() => {
    api.getSettings().then(setSettings).catch(err => {
      console.warn('[settings] initial load failed', err)
    })
  }, [])

  useEffect(() => {
    if (settings.theme === 'auto') {
      document.documentElement.removeAttribute('data-theme')
      localStorage.removeItem('dawncast:theme')
    } else {
      document.documentElement.setAttribute('data-theme', settings.theme)
      localStorage.setItem('dawncast:theme', settings.theme)
    }
  }, [settings.theme])

  const updateSettings = useCallback(async (patch: Partial<Settings>) => {
    const updated = await api.updateSettings(patch)
    setSettings(updated)
  }, [])

  const value: SettingsContextValue = { settings, updateSettings }

  return (
    <SettingsContext.Provider value={value}>
      {children}
    </SettingsContext.Provider>
  )
}
