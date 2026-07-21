import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../api'
import { storageGet, storageSet } from '../lib/storage'
import { ActivityContext, type ActivityContextValue } from './activityContextValue'

// 沿用既有 localStorage key（舊版 ListenedProvider / PlayerRoute 直接寫這幾個 key，前者已移除）。
// 集中到這個 Provider 後，它們降級為「mount 時的初始值 + API 失敗時的 fallback cache」，
// 不再是 source of truth——GET /activity 成功後會覆蓋。
const DATES_KEY = 'dawncast:activity:dates'
const LISTEN_MINUTES_KEY = 'dawncast:activity:listenMinutes'
const LOOKUP_COUNT_KEY = 'dawncast:activity:lookupCount'
const LISTENED_KEY = 'dawncast:listened'

const MAX_STREAK_DATES = 365
// PlayerProvider.persistProgress 每 0.2~1 秒觸發一次；lastPlayed 的 API 呼叫另開節流，
// 避免播放期間每秒打一次網路請求。localStorage 寫入頻率不受影響。
const LAST_PLAYED_SYNC_THROTTLE_MS = 15_000

type ActivityState = {
  readonly streakDates: readonly string[]
  readonly listenMinutes: Readonly<Record<string, number>>
  readonly lookupCount: Readonly<Record<string, number>>
  readonly listenedEpisodeIds: readonly string[]
  readonly lastPlayedEpisodeId: string | null
  readonly lastPlayedPosition: number | null
}

function loadCache(): ActivityState {
  return {
    streakDates: storageGet<string[]>(DATES_KEY) ?? [],
    listenMinutes: storageGet<Record<string, number>>(LISTEN_MINUTES_KEY) ?? {},
    lookupCount: storageGet<Record<string, number>>(LOOKUP_COUNT_KEY) ?? {},
    listenedEpisodeIds: storageGet<string[]>(LISTENED_KEY) ?? [],
    lastPlayedEpisodeId: null,
    lastPlayedPosition: null,
  }
}

export function ActivityProvider({ children }: { readonly children: ReactNode }) {
  const [state, setState] = useState<ActivityState>(loadCache)
  const lastSyncRef = useRef(0)

  useEffect(() => {
    // mount 時用後端資料覆蓋 localStorage cache，換裝置登入同一 user 才看得到一致數字。
    api
      .getActivity()
      .then(remote => {
        setState({
          streakDates: remote.streakDates,
          listenMinutes: remote.listenMinutes,
          lookupCount: remote.lookupCount,
          listenedEpisodeIds: remote.listenedEpisodeIds,
          lastPlayedEpisodeId: remote.lastPlayedEpisodeId ?? null,
          lastPlayedPosition: remote.lastPlayedPosition ?? null,
        })
        storageSet(DATES_KEY, remote.streakDates)
        storageSet(LISTEN_MINUTES_KEY, remote.listenMinutes)
        storageSet(LOOKUP_COUNT_KEY, remote.lookupCount)
        storageSet(LISTENED_KEY, remote.listenedEpisodeIds)
      })
      .catch(() => {
        // API 失敗（離線 / 未登入等）：維持 mount 時讀到的 localStorage cache，不擋 UI。
      })
  }, [])

  const markListened = useCallback((episodeId: string) => {
    const today = new Date().toLocaleDateString('en-CA')
    setState(prev => {
      if (prev.listenedEpisodeIds.includes(episodeId)) return prev
      const streakDates = prev.streakDates.includes(today)
        ? prev.streakDates
        : [...prev.streakDates, today].slice(-MAX_STREAK_DATES)
      const listenedEpisodeIds = [...prev.listenedEpisodeIds, episodeId]
      storageSet(DATES_KEY, streakDates)
      storageSet(LISTENED_KEY, listenedEpisodeIds)
      return { ...prev, streakDates, listenedEpisodeIds }
    })
    void api.patchActivity({ addStreakDate: today, addListenedEpisodeId: episodeId }).catch(err => {
      console.warn('[activity] patchActivity failed', err)
    })
  }, [])

  const addListenMinutes = useCallback((month: string, minutes: number) => {
    if (minutes <= 0) return
    setState(prev => {
      const listenMinutes = {
        ...prev.listenMinutes,
        [month]: (prev.listenMinutes[month] ?? 0) + minutes,
      }
      storageSet(LISTEN_MINUTES_KEY, listenMinutes)
      return { ...prev, listenMinutes }
    })
    void api.patchActivity({ addListenMinutes: { month, minutes } }).catch(err => {
      console.warn('[activity] patchActivity failed', err)
    })
  }, [])

  const addLookupCount = useCallback((month: string, count: number) => {
    if (count <= 0) return
    setState(prev => {
      const lookupCount = {
        ...prev.lookupCount,
        [month]: (prev.lookupCount[month] ?? 0) + count,
      }
      storageSet(LOOKUP_COUNT_KEY, lookupCount)
      return { ...prev, lookupCount }
    })
    void api.patchActivity({ addLookupCount: { month, count } }).catch(err => {
      console.warn('[activity] patchActivity failed', err)
    })
  }, [])

  const setLastPlayed = useCallback(
    (episodeId: string, position: number, opts?: { readonly force?: boolean }) => {
      setState(prev => ({ ...prev, lastPlayedEpisodeId: episodeId, lastPlayedPosition: position }))
      const now = Date.now()
      if (!opts?.force && now - lastSyncRef.current < LAST_PLAYED_SYNC_THROTTLE_MS) return
      lastSyncRef.current = now
      void api.patchActivity({ lastPlayed: { episodeId, position, at: new Date().toISOString() } }).catch(err => {
        console.warn('[activity] patchActivity failed', err)
      })
    },
    [],
  )

  const value: ActivityContextValue = {
    streakDates: state.streakDates,
    listenMinutes: state.listenMinutes,
    lookupCount: state.lookupCount,
    listenedEpisodeIds: new Set(state.listenedEpisodeIds),
    lastPlayedEpisodeId: state.lastPlayedEpisodeId,
    lastPlayedPosition: state.lastPlayedPosition,
    markListened,
    addListenMinutes,
    addLookupCount,
    setLastPlayed,
  }

  return (
    <ActivityContext.Provider value={value}>
      {children}
    </ActivityContext.Provider>
  )
}
