import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Episode } from '../types/episode'

export interface UseEpisodeResult {
  readonly episode: Episode | null
  readonly fetchError: string | null
  /** 這集是不是由 ?orderId= 解析出來的（＝這集是某張點餐訂單交付的那集）。
   *  只有這個情況下才該在播放完成時呼叫 markPlayed——避免任意集數播放
   *  都誤觸發「這張訂單已播放」。 */
  readonly orderId: string | null
  reload(): Promise<void>
}

/** PlayerRoute 單集抓取：優先 ?orderId= 這筆訂單交付的集數，其次 URL 的 id，
 *  都沒有時 fallback 到 listEpisodes()[0]，避免深連結／首頁進入都擋在空畫面。 */
export function useEpisode(id: string | undefined): UseEpisodeResult {
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)
  const [orderId, setOrderId] = useState<string | null>(null)

  const reload = useCallback(async () => {
    setFetchError(null)
    setOrderId(null)
    try {
      // ?orderId= 連結：DailyRoute 帶訂單 id 過來，先查這筆訂單交付的集數；
      // 找不到（尚未生成完成／不歸屬）fallback 到 listEpisodes()[0]，避免擋使用者。
      const orderIdParam = new URLSearchParams(window.location.search).get('orderId')
      if (orderIdParam) {
        const delivered = await api.getDeliveredEpisode(orderIdParam)
        if (delivered) {
          setEpisode(delivered)
          setOrderId(orderIdParam)
          return
        }
      }
      if (id) {
        const data = await api.getEpisode(id)
        setEpisode(data)
        return
      }
      const list = await api.listEpisodes()
      if (list.length === 0) {
        setFetchError('目前沒有可播放的集數')
        return
      }
      const data = await api.getEpisode(list[0].id)
      setEpisode(data)
    } catch {
      setFetchError('節目資料載入失敗，請重新整理頁面')
    }
  }, [id])

  useEffect(() => {
    // 非同步資料載入的標準模式：setState 都在 await 之後才發生，
    // 不會造成 render 迴圈；規則誤報，抑制之。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void reload()
  }, [reload])

  return { episode, fetchError, orderId, reload }
}
