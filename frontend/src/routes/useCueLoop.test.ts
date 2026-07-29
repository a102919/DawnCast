// @vitest-environment happy-dom
// useCueLoop 迴歸測試：焦點是「回捲條件」這一個分支。
//
// 線上出過的災情：位置回報偏低（seek 剛完成、下一幀才更新，或引擎少算段內 offset）時，
// 舊條件「不在 [start, end) 內就回捲」會把這個過渡狀態也算成該回捲，於是每次
// currentTime 更新都重跑一次 seek + play，同一句被無限重啟，聽起來像卡住一直發同一個聲音。
//
// 不裝 @testing-library/react；沿用 state/useSegmentPlayer.test.ts 的 createRoot + happy-dom 作法。

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

import { describe, expect, it, vi } from 'vitest'
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { useCueLoop, type UseCueLoopResult } from './useCueLoop'
import type { Cue, Episode } from '../types/episode'

/** 三句：cue i 佔 [i*1.1, i*1.1+1]，句與句之間留 0.1 秒空隙。 */
function makeEpisode(): Episode {
  const cues: Cue[] = Array.from({ length: 3 }, (_, i) => ({
    index: i, speaker: 'A', text: `line ${i}`, zh: `第 ${i} 行`,
    start: i * 1.1, end: i * 1.1 + 1,
  }))
  return {
    id: 'ep-1',
    title: 'Test',
    audioUrl: null,
    segments: cues.map(c => ({ index: c.index, audioUrl: `https://cdn/${c.index}.mp3`, duration: 1, start: c.start, end: c.end })),
    cues,
  }
}

function mountLoop(episode: Episode) {
  const seekTo = vi.fn()
  const play = vi.fn(async () => undefined)
  const playWithUnlock = vi.fn()
  let last: UseCueLoopResult | null = null

  function Probe({ currentTime, activeCueIdx }: { readonly currentTime: number; readonly activeCueIdx: number }) {
    last = useCueLoop({ episode, currentTime, activeCueIdx, seekTo, play, playWithUnlock })
    return null
  }

  const root: Root = createRoot(document.createElement('div'))
  const render = (currentTime: number, activeCueIdx = 1) => {
    act(() => { root.render(createElement(Probe, { currentTime, activeCueIdx })) })
  }
  return {
    seekTo, play, playWithUnlock, render,
    get result(): UseCueLoopResult {
      if (!last) throw new Error('hook not yet mounted')
      return last
    },
    unmount: () => { act(() => root.unmount()) },
  }
}

describe('useCueLoop', () => {
  it('未開啟循環時不會自動回捲', () => {
    const h = mountLoop(makeEpisode())
    h.render(99)
    expect(h.seekTo).not.toHaveBeenCalled()
    h.unmount()
  })

  it('越過鎖定 cue 尾端時回捲到起點並續播', () => {
    const h = mountLoop(makeEpisode())
    h.render(1.5)
    act(() => { h.result.toggle() }) // 鎖定 cue 1 = [1.1, 2.1]
    h.seekTo.mockClear()

    h.render(2.15) // 已越過 end
    expect(h.seekTo).toHaveBeenCalledWith(1.1)
    expect(h.play).toHaveBeenCalled()
    h.unmount()
  })

  // 這題就是線上災情：位置落在鎖定 cue 的 start 之前（seek 剛完成的過渡幀 / 位置少報
  // offset），舊條件會判定「不在區間內」而每幀重跑 seek + play。
  it('位置還沒追上 cue 起點時不回捲，連續多幀也不會反覆觸發', () => {
    const h = mountLoop(makeEpisode())
    h.render(1.5)
    act(() => { h.result.toggle() })
    h.seekTo.mockClear()
    h.play.mockClear()

    for (const t of [0, 0.3, 0.6, 1.0, 1.09]) h.render(t)
    expect(h.seekTo).not.toHaveBeenCalled()
    expect(h.play).not.toHaveBeenCalled()
    h.unmount()
  })

  it('在鎖定 cue 區間內正常播放不會被打斷', () => {
    const h = mountLoop(makeEpisode())
    h.render(1.5)
    act(() => { h.result.toggle() })
    h.seekTo.mockClear()

    for (const t of [1.2, 1.6, 2.0]) h.render(t)
    expect(h.seekTo).not.toHaveBeenCalled()
    h.unmount()
  })

  it('關閉循環後越過尾端不再回捲', () => {
    const h = mountLoop(makeEpisode())
    h.render(1.5)
    act(() => { h.result.toggle() })
    act(() => { h.result.toggle() })
    h.seekTo.mockClear()

    h.render(2.5)
    expect(h.seekTo).not.toHaveBeenCalled()
    h.unmount()
  })
})
