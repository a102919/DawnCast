import { useEffect, useMemo, useState } from 'react'
import { toast } from 'sonner'
import { CalendarDays, Play } from 'lucide-react'
import { useDailyOrder, useSettings } from '../state'
import { TOPIC_LABELS, formatDateZhTW } from './episodeData'
import type { MockEpisode } from './episodeData'
import { api } from '../api'
import { DailyCalendar } from '../components/daily/DailyCalendar'
import { DailyOrderForm, type DailyOrderFormSubmitResult } from '../components/daily/DailyOrderForm'
import { DailyOrderHistory } from '../components/daily/DailyOrderHistory'
import { DEFAULT_DELIVERY_TIME, nextNDays, toIsoDate } from '../lib/dailyOrderDate'

export function DailyRoute() {
  const { todayDate, orders, getOrder, setOrder, deleteOrder } = useDailyOrder()
  const { settings } = useSettings()
  const [userSelectedDate, setUserSelectedDate] = useState<string>(todayDate)
  const [formExpanded, setFormExpanded] = useState<boolean>(false)
  const [busy, setBusy] = useState(false)
  const [episodes, setEpisodes] = useState<readonly MockEpisode[]>([])

  useEffect(() => {
    api.listEpisodes()
      .then(list => setEpisodes(list))
      .catch(() => { /* 推薦區塊不顯示，不影響訂單流程 */ })
  }, [])

  // 跨日保護：若使用者選的日期已 < todayDate，自動回 todayDate。
  // 不靠 effect 修 state：直接以 todayDate 為下界，避免 cascading render。
  const selectedDate = userSelectedDate < todayDate ? todayDate : userSelectedDate

  // 選日期的副作用：自動展開編輯區塊。行事曆與歷史列點擊都走這條路徑。
  const handleSelectDate = (date: string) => {
    setUserSelectedDate(date)
    setFormExpanded(true)
  }

  const calendarDates = useMemo(() => nextNDays(todayDate, 7), [todayDate])
  // 保險：避免任何異常下 calendarDates 空掉
  const safeCalendarDates =
    calendarDates.length === 7 ? calendarDates : nextNDays(toIsoDate(new Date()), 7)

  const existing = getOrder(selectedDate)
  const formKey = `${selectedDate}-${existing?.updatedAt ?? 'new'}`

  const handleSubmit = async (result: DailyOrderFormSubmitResult) => {
    if (result.kind === 'cancel') {
      setBusy(true)
      try {
        await deleteOrder(selectedDate)
        toast.success('已取消訂單')
      } catch {
        toast.error('取消失敗，請重試')
      } finally {
        setBusy(false)
      }
      return
    }
    setBusy(true)
    try {
      await setOrder(selectedDate, {
        selectedTopics: result.selectedTopics,
        ...(result.specificRequest ? { specificRequest: result.specificRequest } : {}),
        deliveryTime: result.deliveryTime,
        // Phase 4：把表單選的入口類型與長度 tier 帶到 setOrder。
        entryMode: result.entryMode,
        lengthTier: result.lengthTier,
      })
      toast.success(result.kind === 'update' ? '已更新訂單' : '已送出訂單')
    } catch {
      toast.error('送出失敗，請重試')
    } finally {
      setBusy(false)
    }
  }

  const today = formatDateZhTW(todayDate)

  // 推薦 episode:根據 selectedDate 訂單主題挑第一個,無訂單或無匹配時回 episodes[0]
  const recommendedEpisode = useMemo(() => {
    const topics = existing?.selectedTopics ?? []
    if (topics.length > 0) {
      const matched = episodes.find(ep => (topics as readonly string[]).includes(ep.topic))
      if (matched) return matched
    }
    return episodes[0]
  }, [existing, episodes])

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 space-y-6">
      {/* 標頭 */}
      <div className="flex items-center gap-2">
        <div className="w-9 h-9 rounded-lg bg-accent/10 flex items-center justify-center text-accent">
          <CalendarDays size={18} />
        </div>
        <div>
          <h1 className="text-xl font-semibold text-text-primary">每日點餐</h1>
          <p className="text-xs text-text-tertiary mt-0.5">今天 {today}</p>
        </div>
      </div>

      {/* 行事曆 */}
      <DailyCalendar
        today={todayDate}
        dates={safeCalendarDates}
        selectedDate={selectedDate}
        getOrder={getOrder}
        onSelect={handleSelectDate}
      />

      {/* 訂單編輯卡：key 強制讓 selectedDate 變動時重置內部 state。
          collapsed 預設 true（摺疊摘要卡），選日期或點摘要卡按鈕才展開。 */}
      <DailyOrderForm
        key={formKey}
        date={selectedDate}
        existing={existing}
        busy={busy}
        collapsed={!formExpanded}
        onExpand={() => setFormExpanded(true)}
        defaultDeliveryTime={settings.defaultDeliveryTime ?? DEFAULT_DELIVERY_TIME}
        onSubmit={r => void handleSubmit(r)}
      />

      {/* 推薦試聽（只顯示今日 / 未來） */}
      {selectedDate >= todayDate && recommendedEpisode && (
        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-text-tertiary uppercase tracking-wider">先試聽這集</h2>
          <a
            href={`/player?date=${selectedDate}`}
            className="block p-4 rounded-lg border border-border bg-bg-primary hover:border-accent/40 hover:bg-accent/5 transition-colors duration-fast group"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 mb-1 text-xs text-text-tertiary">
                  <span>E{recommendedEpisode.episode}</span>
                  <span>·</span>
                  <span>{TOPIC_LABELS[recommendedEpisode.topic]}</span>
                </div>
                <div className="font-medium text-text-primary text-sm leading-snug">
                  {recommendedEpisode.title}
                </div>
                <div className="text-xs text-text-secondary mt-0.5">{recommendedEpisode.titleZh}</div>
              </div>
              <div className="shrink-0 w-9 h-9 rounded-full bg-accent flex items-center justify-center text-white group-hover:scale-105 transition-transform duration-fast">
                <Play size={14} fill="currentColor" />
              </div>
            </div>
          </a>
        </section>
      )}

      {/* 訂單紀錄 */}
      <DailyOrderHistory
        today={todayDate}
        orders={orders}
        selectedDate={selectedDate}
        onSelectDate={handleSelectDate}
      />
    </div>
  )
}