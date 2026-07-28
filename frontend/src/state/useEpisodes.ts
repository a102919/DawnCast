import { useContext } from 'react'
import { EpisodesContext, type EpisodesContextValue } from './episodesContextValue'

export function useEpisodes(): EpisodesContextValue {
  const ctx = useContext(EpisodesContext)
  if (!ctx) throw new Error('useEpisodes must be used inside EpisodesProvider')
  return ctx
}
