import { useEffect, useRef, useState } from 'react'
import { Play, Loader2 } from 'lucide-react'
import { api } from '../../api'
import { AppError } from '../../api/httpApi'

interface ReplayAudioButtonProps {
  readonly episodeSlug: string
  readonly timestamp: number
}

const REPLAY_DURATION_MS = 5000
const METADATA_TIMEOUT_MS = 8000
// 後端 presign 預設 TTL 7200s，保守一點預留緩衝：剩不到 5 分鐘就重新簽章，
// 避免在 catch 吞錯且 cache 不清→同集永遠重播失敗。
const URL_TTL_SAFETY_MS = 5 * 60 * 1000
interface CachedUrl {
  readonly url: string
  readonly expiresAt: number
}
const audioUrlCache = new Map<string, CachedUrl>()

function waitForMetadata(audio: HTMLAudioElement): Promise<void> {
  if (audio.readyState >= 1) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      audio.removeEventListener('loadedmetadata', onLoaded)
      audio.removeEventListener('error', onError)
      window.clearTimeout(timer)
    }
    const onLoaded = () => {
      cleanup()
      resolve()
    }
    const onError = () => {
      cleanup()
      reject(new AppError('audio_load_failed', 'audio load failed', 0))
    }
    const timer = window.setTimeout(() => {
      cleanup()
      reject(new AppError('audio_load_timeout', 'audio metadata load timeout', 0))
    }, METADATA_TIMEOUT_MS)
    audio.addEventListener('loadedmetadata', onLoaded, { once: true })
    audio.addEventListener('error', onError, { once: true })
  })
}

/** 獨立於 PlayerContext 的迷你播放器：複習時重聽單字出現的那句原音（雙碼理論：語境句+聽覺同時觸發）。
 *
 * 刻意不接 PlayerProvider——離開 /player 後 AudioPlayer 會 unmount 並把 videoRef 設回 null，
 * 這裡呼叫 seekTo 會靜默無效果（見 VocabEntryCard 既有的「跳到」按鈕同樣的競態問題）。
 * episodeSlug 換 audioUrl 走獨立的 api.getEpisode，不牽動任何全域播放狀態。
 */
export function ReplayAudioButton({ episodeSlug, timestamp }: ReplayAudioButtonProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const timerRef = useRef<number | null>(null)
  const [isLoading, setIsLoading] = useState(false)

  // unmount 時清掉 pause timer 並停止 audio，避免背景繼續播 + 舊 timer 觸發後續暫停。
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
      const audio = audioRef.current
      if (audio) {
        audio.pause()
        audio.src = ''
      }
    }
  }, [])

  const handleClick = async () => {
    if (isLoading) return
    setIsLoading(true)
    try {
      const now = Date.now()
      let cached = audioUrlCache.get(episodeSlug)
      if (!cached || cached.expiresAt - now < URL_TTL_SAFETY_MS) {
        const episode = await api.getEpisode(episodeSlug)
        if (!episode.audioUrl) return
        cached = { url: episode.audioUrl, expiresAt: now + URL_TTL_SAFETY_MS }
        audioUrlCache.set(episodeSlug, cached)
      }
      const targetUrl = cached.url
      const audio = audioRef.current ?? new Audio()
      // React Compiler 對 audioRef.current 的追蹤會誤判 src mutation：實務上只是把
      // audio 的 src 設成新 URL 讓瀏覽器重抓媒體，並沒有破壞任何 React invariant。
      // eslint-disable-next-line react-hooks/immutability
      if (audio.src !== targetUrl) audio.src = targetUrl
      audioRef.current = audio
      await waitForMetadata(audio)
      audio.currentTime = timestamp
      await audio.play()
      // 既有舊 timer 還在跑就清掉，避免後續 play 被它提前 pause。
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
      timerRef.current = window.setTimeout(() => {
        audio.pause()
        timerRef.current = null
      }, REPLAY_DURATION_MS)
    } catch {
      // best-effort：播放失敗不阻擋複習流程；TTL 過期或 4xx 已在 catch 內回傳 AppError 給呼叫端 log
      audioUrlCache.delete(episodeSlug)
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <button
      type="button"
      onClick={() => void handleClick()}
      disabled={isLoading}
      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-bg-secondary text-text-secondary hover:text-accent hover:bg-accent/10 transition-colors duration-fast disabled:opacity-50"
    >
      {isLoading ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
      重播原音
    </button>
  )
}
