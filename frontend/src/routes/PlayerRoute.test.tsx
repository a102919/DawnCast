// @vitest-environment happy-dom
// PlayerRoute 測試（回歸鎖：/player/:id 要用 URL 的 id 呼叫 api.getEpisode，
// 不是不管網址是什麼都固定播 listEpisodes()[0]）。

// React 19 對 act() 的環境感知旗標，沒設會跳 warn；不影響測試通過但很吵。
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
//
// 修之前：loadEpisode 不管 useParams 給了什麼 id，一律 listEpisodes() 拿全部集數
// 再取 [0] 呼叫 getEpisode，導致 /player/ep-2 這種深連結永遠播到第一集。
// 修之後：有 id 就直接 api.getEpisode(id)，list[0] fallback 只在無 id（/player）時才用。
//
// 不裝 @testing-library/react，直接用 react-dom/client.createRoot + happy-dom
// 提供的 window/document，跟 DailyOrderProvider.test.tsx 同一套風格。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, type ReactNode } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { PlayerRoute } from './PlayerRoute'
import type { DictEntry } from '../api/types'
import type { Episode } from '../types/episode'
import type { MockEpisode } from '../lib'

const MOCK_LIST: readonly MockEpisode[] = [
  { id: 'ep-1', title: 'Ep One', titleZh: '第一集', topic: 'tech', cefrLevel: 'B1', episode: 1, publishedAt: '2026-07-01' },
  { id: 'ep-2', title: 'Ep Two', titleZh: '第二集', topic: 'tech', cefrLevel: 'B1', episode: 2, publishedAt: '2026-07-02' },
]

function mockEpisodeFor(id: string): Episode {
  return {
    id,
    title: `Episode ${id}`,
    audioUrl: `https://example.com/${id}.mp3`,
    cues: [{ index: 0, speaker: 'Alex', text: 'Hello', zh: '你好', start: 0, end: 1 }],
  }
}

const MOCK_DICT_ENTRY: DictEntry = {
  word: 'hello',
  pos: ['int.'],
  translation: '你好',
  exampleEn: 'Hello there.',
}

// Mock api 模組：spyOn 真物件太繞，直接替換整個 export（跟 DailyOrderProvider.test.tsx 同手法）。
const listEpisodes = vi.fn(async (): Promise<readonly MockEpisode[]> => MOCK_LIST)
const getEpisode = vi.fn(async (id: string): Promise<Episode> => mockEpisodeFor(id))
const getDeliveredEpisode = vi.fn(async (_date: string): Promise<Episode | null> => null)
const lookupDict = vi.fn(async (_word: string): Promise<DictEntry | null> => null)

vi.mock('../api', () => ({
  get api() {
    return { listEpisodes, getEpisode, getDeliveredEpisode, lookupDict }
  },
}))

let playerCurrentTime = 0
let playerIsPlaying = false
let popupEnabled = false
const seekTo = vi.fn()
const play = vi.fn()
const pause = vi.fn()

// PlayerRoute 直接呼叫的 state hooks 全部換成靜態假值：這個測試只關心「URL 的
// id 有沒有正確傳進 api.getEpisode」，不需要真的掛整棵 Provider tree。
vi.mock('../state', () => ({
  usePlayer: () => ({
    currentTime: playerCurrentTime,
    isPlaying: playerIsPlaying,
    duration: 0,
    playbackRate: 1,
    videoRef: { current: null },
    seekTo,
    setVideoRef: vi.fn(),
    play,
    pause,
    setPlaybackRate: vi.fn(),
    loadProgress: () => ({ currentTime: 0, exists: false }),
  }),
  useSettings: () => ({
    settings: {
      popupEnabled,
      playbackRate: 1,
      theme: 'auto',
      preferredTopics: [],
      defaultDeliveryTime: '07:00',
      cefrLevel: 'B1',
    },
    updateSettings: vi.fn(),
  }),
  useDailyOrder: () => ({
    todayDate: '2026-07-17',
    orders: new Map(),
    getOrder: () => null,
    setOrder: vi.fn(),
    deleteOrder: vi.fn(),
    markPlayed: vi.fn(),
  }),
  useActivity: () => ({
    streakDates: [],
    listenMinutes: {},
    lookupCount: {},
    listenedEpisodeIds: new Set<string>(),
    lastPlayedEpisodeId: null,
    lastPlayedPosition: null,
    markListened: vi.fn(),
    addListenMinutes: vi.fn(),
    addLookupCount: vi.fn(),
    setLastPlayed: vi.fn(),
  }),
  useVocab: () => ({
    items: [],
    isLoading: false,
    addVocab: vi.fn(),
    removeVocab: vi.fn(),
    clearVocab: vi.fn(),
    isInVocab: () => false,
    updateCardReview: vi.fn(),
  }),
}))

function Wrapper({ initialPath, children }: { readonly initialPath: string; readonly children: ReactNode }) {
  return (
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/player" element={children} />
        <Route path="/player/:id" element={children} />
      </Routes>
    </MemoryRouter>
  )
}

async function renderAt(initialPath: string): Promise<{ root: Root; container: HTMLDivElement }> {
  const container = document.createElement('div')
  document.body.appendChild(container)
  const root = createRoot(container)

  await act(async () => {
    root.render(<Wrapper initialPath={initialPath}><PlayerRoute /></Wrapper>)
  })
  // loadEpisode 的 await 鏈跑完、讓 setEpisode 產生的 re-render 也在 act 內結算。
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
  return { root, container }
}

const pendingRoots: Root[] = []

async function rerenderAt(root: Root, initialPath: string): Promise<void> {
  await act(async () => {
    root.render(<Wrapper initialPath={initialPath}><PlayerRoute /></Wrapper>)
    await Promise.resolve()
  })
}

function getWord(container: HTMLElement): HTMLSpanElement {
  const word = Array.from(container.querySelectorAll('span')).find(node => node.textContent === 'Hello')
  if (!(word instanceof HTMLSpanElement)) throw new Error('找不到字幕單字')
  return word
}

function getButton(container: HTMLElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll<HTMLButtonElement>('button'))
    .find(el => el.getAttribute('aria-label') === label || el.textContent?.includes(label))
  if (!button) throw new Error(`找不到按鈕：${label}`)
  return button
}

async function click(element: HTMLElement): Promise<void> {
  await act(async () => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await Promise.resolve()
  })
}

beforeEach(() => {
  listEpisodes.mockClear()
  getEpisode.mockClear()
  getDeliveredEpisode.mockClear()
  lookupDict.mockReset()
  lookupDict.mockResolvedValue(null)
  seekTo.mockClear()
  play.mockClear()
  pause.mockClear()
  playerCurrentTime = 0
  playerIsPlaying = false
  popupEnabled = false
})

afterEach(async () => {
  await act(async () => {
    for (const r of pendingRoots.splice(0)) r.unmount()
  })
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

describe('PlayerRoute：/player/:id 要播 URL 指定的集數', () => {
  it('id=ep-2 時 api.getEpisode 收到的參數是 ep-2，不是 list 裡第一筆 ep-1', async () => {
    const { root } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    expect(getEpisode).toHaveBeenCalledTimes(1)
    expect(getEpisode).toHaveBeenCalledWith('ep-2')
    // 修之前的邏輯會先呼叫 listEpisodes() 再用 list[0].id 呼叫 getEpisode；
    // 有 id 時完全不該碰 listEpisodes。
    expect(listEpisodes).not.toHaveBeenCalled()
  })

  it('無 id（/player）時才 fallback 到 listEpisodes()[0]', async () => {
    const { root } = await renderAt('/player')
    pendingRoots.push(root)

    expect(listEpisodes).toHaveBeenCalledTimes(1)
    expect(getEpisode).toHaveBeenCalledWith('ep-1')
  })

  it('啟用詞卡關閉時，點字幕單字不會暫停或查詢字典', async () => {
    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getWord(container))

    expect(pause).not.toHaveBeenCalled()
    expect(lookupDict).not.toHaveBeenCalled()
  })
})

describe('PlayerRoute：點單字時的播放控制', () => {
  it('播放中點字：點擊時就 pause，關閉後恢復 play', async () => {
    popupEnabled = true
    playerIsPlaying = true
    let resolveLookup!: (entry: DictEntry | null) => void
    lookupDict.mockImplementationOnce(() => new Promise<DictEntry | null>(r => { resolveLookup = r }))

    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getWord(container))
    // 必須在字典 Promise resolve 之前就暫停，使用者點字瞬間要 stop 音訊
    expect(pause).toHaveBeenCalledTimes(1)

    await act(async () => {
      resolveLookup(MOCK_DICT_ENTRY)
      await Promise.resolve()
    })
    await rerenderAt(root, '/player/ep-2')

    expect(lookupDict).toHaveBeenCalledWith('hello')

    await click(getButton(container, '關閉詞卡'))
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('點字前已暫停：關閉詞卡後仍維持暫停', async () => {
    popupEnabled = true
    playerIsPlaying = false
    lookupDict.mockResolvedValueOnce(MOCK_DICT_ENTRY)

    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getWord(container))
    expect(pause).not.toHaveBeenCalled()
    expect(lookupDict).toHaveBeenCalledWith('hello')

    await click(getButton(container, '關閉詞卡'))
    expect(play).not.toHaveBeenCalled()
  })

  it('查詢失敗重試不覆寫播放快照：恢復路徑仍正確', async () => {
    popupEnabled = true
    playerIsPlaying = true
    lookupDict.mockRejectedValueOnce(new Error('boom'))
    lookupDict.mockResolvedValueOnce(MOCK_DICT_ENTRY)

    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getWord(container))
    expect(pause).toHaveBeenCalledTimes(1)

    const retry = getButton(container, '重試')
    await click(retry)
    expect(lookupDict).toHaveBeenCalledTimes(2)

    await click(getButton(container, '關閉詞卡'))
    // 重試不應因為目前「未播放」就清掉恢復旗標
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('重聽這句：seek 正確且只 play 一次（不重複觸發）', async () => {
    popupEnabled = true
    playerIsPlaying = true
    lookupDict.mockResolvedValueOnce(MOCK_DICT_ENTRY)

    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getWord(container))
    expect(pause).toHaveBeenCalledTimes(1)

    await click(getButton(container, '重聽這句'))
    expect(seekTo).toHaveBeenLastCalledWith(0)
    expect(play).toHaveBeenCalledTimes(1)
  })
})

describe('PlayerRoute：單句循環', () => {
  it('開啟循環會從目前 cue 起點開始播放，關閉時不改時間也不額外 play/pause', async () => {
    playerCurrentTime = 0.5
    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    const toggle = getButton(container, '開啟單句循環')
    expect(toggle.getAttribute('aria-pressed')).toBe('false')

    await click(toggle)
    expect(seekTo).toHaveBeenLastCalledWith(0)
    expect(play).toHaveBeenCalledTimes(1)

    await rerenderAt(root, '/player/ep-2')
    const toggledOn = getButton(container, '關閉單句循環')
    expect(toggledOn.getAttribute('aria-pressed')).toBe('true')

    await click(toggledOn)
    expect(seekTo).toHaveBeenCalledTimes(1)
    expect(play).toHaveBeenCalledTimes(1)
    expect(pause).not.toHaveBeenCalled()
  })

  it('循環中越過 cue end 會自動 seek 回起點並 play', async () => {
    playerCurrentTime = 0.5
    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getButton(container, '開啟單句循環'))
    play.mockClear()
    seekTo.mockClear()

    playerCurrentTime = 1.5 // cues[0].end=1，跨越邊界
    await rerenderAt(root, '/player/ep-2')

    expect(seekTo).toHaveBeenLastCalledWith(0)
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('循環中開詞卡暫停：關閉後繼續循環', async () => {
    popupEnabled = true
    playerIsPlaying = true
    playerCurrentTime = 0.5
    lookupDict.mockResolvedValueOnce(MOCK_DICT_ENTRY)

    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getButton(container, '開啟單句循環'))
    expect(play).toHaveBeenCalledTimes(1)

    await click(getWord(container))
    expect(pause).toHaveBeenCalledTimes(1)

    await click(getButton(container, '關閉詞卡'))
    expect(play).toHaveBeenCalledTimes(2)

    // loop 旗標仍存在，下一輪越過 end 還是會 seek 回 start
    play.mockClear()
    seekTo.mockClear()
    playerCurrentTime = 1.5
    await rerenderAt(root, '/player/ep-2')
    expect(seekTo).toHaveBeenLastCalledWith(0)
    expect(play).toHaveBeenCalledTimes(1)
  })

  it('循環中按下一句會更新 lock 目標，不會立即跳回舊句', async () => {
    // 預設 fixture 只有一句，這個 case 改寫成 2 句
    getEpisode.mockResolvedValueOnce({
      id: 'ep-2',
      title: 'Episode ep-2',
      audioUrl: 'https://example.com/ep-2.mp3',
      cues: [
        { index: 0, speaker: 'Alex', text: 'Hello', zh: '你好', start: 0, end: 1 },
        { index: 1, speaker: 'Sam', text: 'World', zh: '世界', start: 1, end: 2 },
      ],
    })
    playerCurrentTime = 0.5
    const { root, container } = await renderAt('/player/ep-2')
    pendingRoots.push(root)

    await click(getButton(container, '開啟單句循環'))
    expect(seekTo).toHaveBeenLastCalledWith(0)

    await click(getButton(container, '下一句'))
    expect(seekTo).toHaveBeenLastCalledWith(1)
  })
})
