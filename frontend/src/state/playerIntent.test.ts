import { describe, expect, it } from 'vitest'
import {
  playerReducer, toPublicFields,
  type MainPlayerState, type MainPlayerAction,
} from './playerIntent'

const STATES: readonly MainPlayerState['kind'][] = ['idle', 'loading', 'error', 'paused', 'playing']
const ACTIONS: readonly MainPlayerAction['type'][] = [
  'LOAD_STARTED', 'LOAD_SUCCEEDED', 'LOAD_FAILED', 'LOAD_CLEARED', 'PLAYBACK_STARTED', 'PLAYBACK_STOPPED',
]

/** 窮舉全部 5×6 組合的期望轉場，跟 playerReducer 本身的 switch 分開寫，
 *  才是真的在驗證行為而不是把實作抄一遍。 */
function expectedNext(from: MainPlayerState['kind'], action: MainPlayerAction['type']): MainPlayerState['kind'] {
  switch (action) {
    case 'LOAD_STARTED': return 'loading'
    case 'LOAD_SUCCEEDED': return from === 'loading' ? 'paused' : from
    case 'LOAD_FAILED': return from === 'loading' ? 'error' : from
    case 'LOAD_CLEARED': return 'idle'
    case 'PLAYBACK_STARTED': return from === 'paused' || from === 'playing' ? 'playing' : from
    case 'PLAYBACK_STOPPED': return from === 'playing' || from === 'paused' ? 'paused' : from
  }
}

describe('playerReducer：窮舉 5 state × 6 action', () => {
  for (const from of STATES) {
    for (const action of ACTIONS) {
      it(`${from} + ${action} → ${expectedNext(from, action)}`, () => {
        const next = playerReducer({ kind: from }, { type: action })
        expect(next.kind).toBe(expectedNext(from, action))
      })
    }
  }
})

describe('toPublicFields：exhaustive，isPlaying 恆蘊含 loadState===ready', () => {
  const table: readonly [MainPlayerState['kind'], 'idle' | 'loading' | 'ready' | 'error', boolean][] = [
    ['idle', 'idle', false],
    ['loading', 'loading', false],
    ['error', 'error', false],
    ['paused', 'ready', false],
    ['playing', 'ready', true],
  ]
  for (const [kind, loadState, isPlaying] of table) {
    it(`${kind} → loadState=${loadState}, isPlaying=${isPlaying}`, () => {
      expect(toPublicFields({ kind })).toEqual({ loadState, isPlaying })
    })
  }
})

describe('playerReducer fuzz：任何 action 序列都不丟例外，且 invariant 恆成立', () => {
  it('200 條隨機序列 × 50 步', () => {
    for (let run = 0; run < 200; run++) {
      let state: MainPlayerState = { kind: 'idle' }
      for (let step = 0; step < 50; step++) {
        const action: MainPlayerAction = { type: ACTIONS[Math.floor(Math.random() * ACTIONS.length)]! }
        expect(() => { state = playerReducer(state, action) }).not.toThrow()
        const fields = toPublicFields(state)
        if (fields.isPlaying) expect(fields.loadState).toBe('ready')
      }
    }
  })
})
