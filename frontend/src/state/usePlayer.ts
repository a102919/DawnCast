import { useContext } from 'react'
import { PlayerContext, type PlayerContextValue } from './playerContextValue'

export function usePlayer(): PlayerContextValue {
  const ctx = useContext(PlayerContext)
  if (!ctx) throw new Error('usePlayer must be used inside PlayerProvider')
  return ctx
}
