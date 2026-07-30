import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { api, type VocabItem } from '../api'
import { toIsoDate } from '../lib/dailyOrderDate'
import { applyQuizRound as computeQuizRound } from '../lib/quiz'
import { STATUS_REVIEW } from '../lib/srs'
import { VocabContext, type VocabContextValue } from './vocabContextValue'

// 二元滑卡對映 q4/q1：sm2(q4) 的 ease 增量恰為 0，ease 只降不升是刻意接受的取捨
// （interval 仍每輪 ×ease 成長）；拼字 ClozeCard 答對給 q5，是唯一的 ease 成長通道。
// 精熟唯一路徑是畢業測驗（lib/quiz.applyQuizRound），複習評分不再改 status。
function sm2(quality: number, prevInterval: number, prevEase: number): { interval: number; ease: number } {
  const ease = Math.max(1.3, prevEase + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
  if (quality < 3) return { interval: 1, ease }
  if (prevInterval <= 1) return { interval: 6, ease }
  return { interval: Math.round(prevInterval * prevEase), ease }
}

function todayPlusDays(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return toIsoDate(d)
}

export function VocabProvider({ children }: { readonly children: ReactNode }) {
  const [items, setItems] = useState<VocabItem[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 抓單字但不直接 setState：caller 拿到 list 後才 set，避免 effect 內 set 觸發
  // cascading render（react-hooks/set-state-in-effect）。
  const fetchVocab = useCallback(async () => {
    return api.listVocab()
  }, [])

  // 第一次 mount：抓單字，期間依賴 useState(true)/useState(null) 的初始值，
  // 整段 await 後才 set——plugin 不會把 async callback 算成同步 set。
  useEffect(() => {
    const signal = { cancelled: false }
    fetchVocab()
      .then(list => { if (!signal.cancelled) setItems(list) })
      .catch((err: unknown) => {
        if (signal.cancelled) return
        console.error('listVocab failed:', err)
        setError(err instanceof Error ? err.message : '載入單字本失敗')
      })
      .finally(() => {
        if (!signal.cancelled) setIsLoading(false)
      })
    return () => { signal.cancelled = true }
  }, [fetchVocab])

  const reload = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const list = await fetchVocab()
      setItems(list)
    } catch (err) {
      console.error('listVocab failed:', err)
      setError(err instanceof Error ? err.message : '載入單字本失敗')
    } finally {
      setIsLoading(false)
    }
  }, [fetchVocab])

  const addVocab = useCallback(async (item: Omit<VocabItem, 'id' | 'createdAt'>) => {
    const newItem = await api.addVocab(item)
    setItems(prev => {
      if (prev.some(v => v.id === newItem.id)) return prev
      return [newItem, ...prev]
    })
  }, [])

  const removeVocab = useCallback(async (id: string) => {
    await api.removeVocab(id)
    setItems(prev => prev.filter(v => v.id !== id))
  }, [])

  const clearVocab = useCallback(async () => {
    await api.clearVocab()
    setItems([])
  }, [])

  const isInVocab = useCallback((lemma: string) => {
    return items.some(v => v.lemma === lemma)
  }, [items])

  const updateCardReview = useCallback(async (id: string, quality: number, opts?: { readonly mode?: 'review' | 'practice' }) => {
    const item = items.find(i => i.id === id)
    if (!item) {
      console.warn(`updateCardReview: item ${id} 不存在，略過`)
      return
    }
    const mode = opts?.mode ?? 'review'
    // 練習模式：答對不寫 DB、保留既有排程；答錯把 nextReview 提前到明天當 lapse 訊號。
    if (mode === 'practice') {
      if (quality >= 3) return
      const nextReview = todayPlusDays(1)
      await api.updateVocab(id, { nextReview })
      setItems(prev => prev.map(v => v.id === id ? { ...v, nextReview } : v))
      return
    }
    const { interval, ease } = sm2(quality, item.interval ?? 1, item.ease ?? 2.5)
    const nextReview = todayPlusDays(interval)
    await api.updateVocab(id, { nextReview, interval, ease })
    setItems(prev => prev.map(v => v.id === id ? { ...v, nextReview, interval, ease } : v))
  }, [items])

  // 通過學習模式：進入 SRS 複習，明天首複。
  const completeLearning = useCallback(async (id: string) => {
    const patch = { status: STATUS_REVIEW, nextReview: todayPlusDays(1), interval: 1, ease: 2.5 }
    await api.updateVocab(id, patch)
    setItems(prev => prev.map(v => v.id === id ? { ...v, ...patch } : v))
  }, [])

  // 畢業測驗一輪結果：全對 streak+1（連 2 輪 → 精熟），有錯 streak 歸零回複習。
  const applyQuizRound = useCallback(async (id: string, passed: boolean) => {
    const item = items.find(i => i.id === id)
    if (!item) {
      console.warn(`applyQuizRound: item ${id} 不存在，略過`)
      return
    }
    const patch = computeQuizRound(item, passed)
    await api.updateVocab(id, patch)
    setItems(prev => prev.map(v => v.id === id ? { ...v, ...patch } : v))
  }, [items])

  // 精熟字一鍵重新加入複習。
  const reviveVocab = useCallback(async (id: string) => {
    const patch = { status: STATUS_REVIEW, quizPassStreak: 0, interval: 7, nextReview: toIsoDate(new Date()) }
    await api.updateVocab(id, patch)
    setItems(prev => prev.map(v => v.id === id ? { ...v, ...patch } : v))
  }, [])

  const value: VocabContextValue = {
    items, isLoading, error, reload,
    addVocab, removeVocab, clearVocab, isInVocab,
    updateCardReview, completeLearning, applyQuizRound, reviveVocab,
  }

  return (
    <VocabContext.Provider value={value}>
      {children}
    </VocabContext.Provider>
  )
}
