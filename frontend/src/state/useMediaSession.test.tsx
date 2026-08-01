// @vitest-environment happy-dom
// useMediaSession：iOS Now Playing / Android MediaSession 拿到的 position state
// 必須是整集 episode-wide，不是 per-segment 1–3 秒。這份測試守住 hook 推 native API
// 的時機與數值。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { useEffect, useState } from 'react'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useMediaSession } from './useMediaSession'
import type { Episode } from '../types/episode'

interface MediaSessionFake {
  metadata: MediaMetadata | null
  playbackState: MediaSessionPlaybackState
  setActionHandler: ReturnType<typeof vi.fn>
  setPositionState: ReturnType<typeof vi.fn>
}

function makeEpisode(id: string, title = 'Ep'): Episode {
  return { id, title, audioUrl: null, segments: [], cues: [] }
}

interface State {
  episode: Episode | null
  isPlaying: boolean
  currentTime: number
  duration: number
  playbackRate: number
}

interface Probe {
  setState(s: Partial<State>): void
}

function mountHook(): { root: Root; probe: () => Probe; unmount: () => void } {
  const state: State = { episode: null, isPlaying: false, currentTime: 0, duration: 0, playbackRate: 1 }
  let probeRef: Probe | null = null
  function Component() {
    const [, set] = useState(0)
    useMediaSession({
      episode: state.episode,
      isPlaying: state.isPlaying,
      currentTime: state.currentTime,
      duration: state.duration,
      playbackRate: state.playbackRate,
      getCurrentTime: () => state.currentTime,
      play: () => undefined,
      pause: () => undefined,
      seekTo: () => undefined,
    })
    useEffect(() => {
      probeRef = {
        setState(s) {
          if (s.episode !== undefined) state.episode = s.episode
          if (s.isPlaying !== undefined) state.isPlaying = s.isPlaying
          if (s.currentTime !== undefined) state.currentTime = s.currentTime
          if (s.duration !== undefined) state.duration = s.duration
          if (s.playbackRate !== undefined) state.playbackRate = s.playbackRate
          // 用 act() 確保 re-render + effect 在 expect 之前同步 flush，
          // 否則 React 19 會把 setState 排程延後，spy 還沒收到呼叫。
          act(() => { set(x => x + 1) })
        },
      }
    })
    return null
  }
  const container = document.createElement('div')
  const root = createRoot(container)
  act(() => { root.render(createElement(Component)) })
  // 初始 mount episode=null，effect 會主動呼叫一次 setPositionState() 清掉 OS state，
  // 這是預期行為但會污染 spy；測試要的是「後續 setState 觸發的呼叫」，所以 mount 後清掉。
  fake.setPositionState.mockClear()
  return {
    root,
    probe: () => {
      if (!probeRef) throw new Error('probe not mounted')
      return probeRef
    },
    unmount: () => act(() => root.unmount()),
  }
}

let fake: MediaSessionFake

beforeEach(() => {
  fake = {
    metadata: null,
    playbackState: 'none',
    setActionHandler: vi.fn(),
    setPositionState: vi.fn(),
  }
  // happy-dom 沒內建 MediaSession/MediaMetadata，需手動掛上。
  ;(globalThis as unknown as { MediaMetadata: unknown }).MediaMetadata = class {
    constructor(init: MediaMetadataInit) { Object.assign(this, init) }
  }
  Object.defineProperty(navigator, 'mediaSession', {
    value: fake,
    configurable: true,
    writable: true,
  })
})

afterEach(() => {
  // happy-dom 的 navigator 屬性可能不可刪，刪不掉就丟回空 stub。
  Object.defineProperty(navigator, 'mediaSession', {
    value: undefined,
    configurable: true,
    writable: true,
  })
  delete (globalThis as unknown as { MediaMetadata?: unknown }).MediaMetadata
})

describe('useMediaSession.setPositionState', () => {
  it('episode=null：清掉 OS state 並重置 dedup key', () => {
    const h = mountHook()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 5 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 5, playbackRate: 1 })

    h.probe().setState({ episode: null })
    expect(fake.setPositionState).toHaveBeenLastCalledWith()

    // 再設新 episode 必須重推（dedup key 已被 null 觸發清掉）
    fake.setPositionState.mockClear()
    h.probe().setState({ episode: makeEpisode('e2'), duration: 30, currentTime: 0 })
    expect(fake.setPositionState).toHaveBeenCalledTimes(1)
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 30, position: 0, playbackRate: 1 })

    h.unmount()
  })

  it('duration<=0 / NaN：不呼叫 native API', () => {
    const h = mountHook()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 0, currentTime: 5 })
    expect(fake.setPositionState).not.toHaveBeenCalled()
    h.probe().setState({ episode: makeEpisode('e1'), duration: NaN, currentTime: 5 })
    expect(fake.setPositionState).not.toHaveBeenCalled()
    h.unmount()
  })

  it('position clamp：超過 duration 會被收進 duration，越界負值會被抬到 0', () => {
    const h = mountHook()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 999 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 50, playbackRate: 1 })
    h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: -5 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 0, playbackRate: 1 })
    h.unmount()
  })

  it('playbackRate<=0 / NaN：fallback 1', () => {
    const h = mountHook()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 5, playbackRate: 0 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 5, playbackRate: 1 })
    h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 5, playbackRate: -1.5 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 5, playbackRate: 1 })
    h.unmount()
  })

  it('同整秒內多次更新只推一次；跨整秒重推', () => {
    const h = mountHook()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 180, currentTime: 5 })
    h.probe().setState({ episode: makeEpisode('e1'), duration: 180, currentTime: 5.1 })
    h.probe().setState({ episode: makeEpisode('e1'), duration: 180, currentTime: 5.4 })
    h.probe().setState({ episode: makeEpisode('e1'), duration: 180, currentTime: 5.9 })
    expect(fake.setPositionState).toHaveBeenCalledTimes(1)

    h.probe().setState({ episode: makeEpisode('e1'), duration: 180, currentTime: 6.1 })
    expect(fake.setPositionState).toHaveBeenCalledTimes(2)
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 180, position: 6.1, playbackRate: 1 })

    h.unmount()
  })

  it('180s episode 跨 3s segment 邊界仍維持整集 duration 與 episode-wide position', () => {
    const h = mountHook()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 180, currentTime: 2.9 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 180, position: 2.9, playbackRate: 1 })
    h.probe().setState({ episode: makeEpisode('e1'), duration: 180, currentTime: 3.1 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 180, position: 3.1, playbackRate: 1 })
    h.unmount()
  })

  it('換集、playing 切換、rate 變更都會立刻重推', () => {
    const h = mountHook()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 0, isPlaying: true })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 0, playbackRate: 1 })

    h.probe().setState({ episode: makeEpisode('e1'), isPlaying: false })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 0, playbackRate: 1 })

    h.probe().setState({ episode: makeEpisode('e1'), playbackRate: 1.5 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 0, playbackRate: 1.5 })

    h.probe().setState({ episode: makeEpisode('e2'), duration: 30, currentTime: 10, playbackRate: 1 })
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 30, position: 10, playbackRate: 1 })

    h.unmount()
  })

  it('setPositionState 拋 InvalidStateError：hook 不爆，下次有效 state 仍會重推', () => {
    const h = mountHook()
    // 在 mount 後設定，確保 throw 是被「episode setState 觸發的那次」消耗，
    // 而不是 mount 時 episode=null 的清 OS state 那次。
    fake.setPositionState.mockImplementationOnce(() => {
      throw new DOMException('InvalidStateError', 'InvalidStateError')
    })
    // 拋錯被吞掉，沒崩
    expect(() => h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 0 })).not.toThrow()

    // 拋錯後 ref 沒被更新，下次有效 state 必須再送一次（不可被 dedup 鎖住）。
    fake.setPositionState.mockClear()
    h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 1 })
    expect(fake.setPositionState).toHaveBeenCalledTimes(1)
    expect(fake.setPositionState).toHaveBeenLastCalledWith({ duration: 50, position: 1, playbackRate: 1 })

    h.unmount()
  })

  it('沒有 setPositionState：hook 不爆', () => {
    Object.defineProperty(navigator, 'mediaSession', {
      value: { metadata: null, playbackState: 'none', setActionHandler: vi.fn() },
      configurable: true,
      writable: true,
    })
    const h = mountHook()
    expect(() => h.probe().setState({ episode: makeEpisode('e1'), duration: 50, currentTime: 5 })).not.toThrow()
    h.unmount()
  })
})
