import { createContextHook } from './createContextHook'
import { PlayerContext, type PlayerContextValue } from './playerContextValue'

export const usePlayer: () => PlayerContextValue = createContextHook(
  PlayerContext,
  'usePlayer',
  'PlayerProvider',
)
