// @vitest-environment happy-dom
// PlayerProvider.setCurrentEpisode：同一集重推（例：首頁→再點回同一集）不能砍掉正在播放的
// 音訊重新 loadEpisode（會清 buffer cache + currentTime 砍回 0），只有真的換集才需要重載。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { act, useEffect, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PlayerProvider } from './PlayerProvider'
import { usePlayer } from './usePlayer'
import type { PlayerContextValue } from './playerContextValue'
import type { SegmentPlayer } from './useSegmentPlayer'
import type { Episode } from '../types/episode'

const loadEpisode = vi.fn()

vi.mock('./useSegmentPlayer', () => ({
  useSegmentPlayer: (): SegmentPlayer => ({
    loadState: 'ready',
    isPlaying: false,
    currentTime: 0,
    duration: 0,
    playbackRate: 1,
    muted: false,
    unlock: vi.fn(),
    loadEpisode,
    play: vi.fn(),
    pause: vi.fn(),
    seekTo: vi.fn(),
    seekToWord: vi.fn(() => false),
    setPlaybackRate: vi.fn(),
    setMuted: vi.fn(),
    playSegment: vi.fn(),
  }),
}))

function makeEpisode(id: string): Episode {
  return { id, title: `Ep ${id}`, audioUrl: null, segments: [], cues: [] }
}

let probe: PlayerContextValue | null = null
function Probe() {
  const value = usePlayer()
  useEffect(() => { probe = value })
  return null
}

function renderProvider(): Root {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)
  act(() => {
    root.render(
      <PlayerProvider lastPlayedEpisodeId={null} lastPlayedPosition={null} setLastPlayed={() => undefined}>
        <Probe />
      </PlayerProvider> as ReactNode,
    )
  })
  return root
}

afterEach(() => {
  loadEpisode.mockClear()
  probe = null
  document.body.innerHTML = ''
})

describe('PlayerProvider.setCurrentEpisode', () => {
  it('同 id、不同物件參考重推：只 loadEpisode 一次', () => {
    const root = renderProvider()
    act(() => probe!.setCurrentEpisode(makeEpisode('ep-1')))
    expect(loadEpisode).toHaveBeenCalledTimes(1)

    act(() => probe!.setCurrentEpisode(makeEpisode('ep-1')))
    expect(loadEpisode).toHaveBeenCalledTimes(1)

    act(() => root.unmount())
  })

  it('換成不同 id：重新 loadEpisode', () => {
    const root = renderProvider()
    act(() => probe!.setCurrentEpisode(makeEpisode('ep-1')))
    act(() => probe!.setCurrentEpisode(makeEpisode('ep-2')))
    expect(loadEpisode).toHaveBeenCalledTimes(2)

    act(() => root.unmount())
  })
})
