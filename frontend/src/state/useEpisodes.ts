import { createContextHook } from './createContextHook'
import { EpisodesContext, type EpisodesContextValue } from './episodesContextValue'

export const useEpisodes: () => EpisodesContextValue = createContextHook(
  EpisodesContext,
  'useEpisodes',
  'EpisodesProvider',
)
