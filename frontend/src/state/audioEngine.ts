/** 原生 <audio> 引擎：三個常駐 HTMLAudioElement（主播放輪替 A/B + 試聽專用）。
 *
 * 取代舊 Web Audio 鏈（AudioBufferSourceNode → SoundTouch worklet → gain →
 * MediaStreamAudioDestinationNode → 隱藏 <audio>）。那條鏈的每一環都有不可控延遲：
 * SoundTouch 要吃滿處理窗才出聲（句首被吃）、輸入結束殘留 tail 不保證 flush（句尾被吃）、
 * source.onended 代表「餵料完」不是「喇叭播完」——句子邊界切字是結構性的，修不完。
 * 原生 <audio> 的 ended 事件＝檔案真正播完，邊界切字物理上不可能發生；而舊鏈存在的
 * 兩個理由——變速不變調、iOS 背景 session——瀏覽器都原生提供：
 * playbackRate + preservesPitch（現代瀏覽器預設保留音高），真實 <audio> 元素本身
 * 就是合法的 media session（鎖屏/動態島直接認得）。
 *
 * 主播放用 A/B 兩個元素輪替：播第 i 段時把第 i+1 段的 URL 丟進閒置元素 preload，
 * 句間 0.3s/0.7s 的既有停頓足夠遮住切換。試聽（單字/整句抽樣）走第三個專用元素，
 * 音量固定 DUCK_LEVEL，不碰主播放元素的 src/position——「試聽不干擾主播放游標」
 * 由元素隔離保證，不靠旗標。
 *
 * iOS Safari：媒體元素的播放授權是「每個元素」在 user gesture 內呼叫過 play() 才拿到。
 * 自動接播下一段（gap timer 觸發的 play()）不在 gesture 內，所以 unlock() 要在使用者
 * 點播放的同步呼叫堆疊內，把三個元素都 play()+pause() 一輪拿授權（沒 src 的先塞
 * 無聲 WAV）。授權拿到後終身有效。
 *
 * iOS 上 el.volume 唯讀（靜默忽略）：音量交給硬體鍵，程式面只保留 muted（iOS 可設）。
 * 試聽的 DUCK_LEVEL 在 iOS 上會降級成原音量，可接受。
 *
 * iOS Safari 額外限制：`<audio>` 元素必須在 DOM 內才會真的出聲。`new Audio()`
 * 拿到的 detached 元素即使 play() 也會被瀏覽器 silent reject（desktop 不受限
 * 但實機很重要）。createAudioEngine() 內建隱形 container，把三個元素 append
 * 進去；測試環境（happy-dom/jsdom）沒 document.body 時降級成 detached 模式。
 */

export const DUCK_LEVEL = 0.3

/** iOS 解鎖用最短無聲 WAV（8 samples @ 8kHz）。 */
const SILENT_WAV =
  'data:audio/wav;base64,UklGRiwAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQgAAACAgICAgICAgA=='

export interface StartPlaybackArgs {
  readonly url: string
  readonly globalStartSec: number
  readonly offsetSec: number
  /** 有值＝試聽（走試聽專用元素、限定播放長度）；無值＝主播放（播到檔尾）。 */
  readonly durationSec?: number
  readonly rate: number
}

export interface PlaybackHandle {
  readonly el: HTMLAudioElement
  readonly globalStartSec: number
  readonly offsetSec: number
}

export interface AudioEngine {
  /** 必須在 user gesture 的同步呼叫堆疊內執行（見檔案頂端 iOS 說明）。
   *  帶 targetUrl 時會對該 URL 所在的 element 做 play/pause 同步解鎖（iOS 17 必要）。 */
  unlock(targetUrl?: string): void
  /** 把 URL 丟進閒置元素開始載入；resolve true＝可播、false＝載入失敗。
   *  同一個 URL 已在某元素上就緒時直接 resolve，不重載。 */
  preload(url: string): Promise<boolean>
  startPlayback(args: StartPlaybackArgs, onEnded: () => void, onPlayRejected: () => void): PlaybackHandle
  /** 回傳停止當下的全域播放位置（秒），呼叫端拿去存 pausedAt。 */
  stop(handle: PlaybackHandle): number
  setRate(handle: PlaybackHandle, rate: number): void
  currentPositionSec(handle: PlaybackHandle): number
  setMuted(m: boolean): void
}

/** preservesPitch 未進所有 TS lib/瀏覽器版本，連同 webkit 前綴一起設。 */
function setPreservesPitch(el: HTMLAudioElement): void {
  const anyEl = el as HTMLAudioElement & { preservesPitch?: boolean; webkitPreservesPitch?: boolean }
  anyEl.preservesPitch = true
  anyEl.webkitPreservesPitch = true
}

/** 隱形容器：給 audio 元素一個 DOM 落腳處（iOS Safari 必要）；不影響 layout、不
 *  阻擋點擊、不被輔助技術讀到。測試環境 fake Audio 沒 Element prototype 時
 *  降級成 detached（host 仍會 append 進 body，但 fake 不進 host）。 */
function attachToDOM(els: readonly HTMLAudioElement[]): void {
  const body = typeof document !== 'undefined' ? document.body : null
  if (!body) return
  // 防止 HMR / Vitest 多 describe 重複掛載時 body 累積舊 host：拔掉舊的，元素斷開
  // parent 後可 GC。
  body.querySelectorAll('[data-audio-host]').forEach(el => el.remove())
  const host = document.createElement('div')
  host.setAttribute('aria-hidden', 'true')
  host.setAttribute('data-audio-host', '')
  host.style.cssText = 'position:absolute;width:0;height:0;opacity:0;pointer-events:none;overflow:hidden;'
  for (const el of els) {
    if (!(el instanceof Element)) continue
    host.appendChild(el)
  }
  body.appendChild(host)
}

export function createAudioEngine(): AudioEngine {
  const makeEl = (volume: number): HTMLAudioElement => {
    const el = new Audio()
    el.preload = 'auto'
    el.volume = volume
    setPreservesPitch(el)
    return el
  }
  const mainA = makeEl(1)
  const mainB = makeEl(1)
  const previewEl = makeEl(DUCK_LEVEL)
  const all = [mainA, mainB, previewEl] as const
  attachToDOM(all)

  let activeMainEl: HTMLAudioElement | null = null
  let muted = false
  /** 每次 startPlayback 換一個 token；元素被新播放接手後，舊 handle 掛的延遲
   *  callback（loadedmetadata 補 seek、試聽定時停止）憑 token 判斷自己已過期。 */
  const tokens = new WeakMap<HTMLAudioElement, symbol>()
  const handleTokens = new WeakMap<PlaybackHandle, symbol>()
  /** 哪些元素已完成 iOS 解鎖。解鎖過的不重複觸發，避免 race condition。 */
  const unlockedSet = new WeakSet<HTMLAudioElement>()

  function pickMainEl(url: string): HTMLAudioElement {
    // 已經載入這 URL 的元素接著用（preload 過、避免重 fetch）。
    if (mainA.src && mainA.src.includes(url)) return mainA
    if (mainB.src && mainB.src.includes(url)) return mainB
    // 接播必須換另一個元素，否則同一段 ended 接下一段時 src 蓋上去、記憶體
    // 仍指向「剛播完」的元素，play() 從新 src 啟動仍會在同一個 hardware path
    // —— 不同元素讓兩個 src 在背景同時存在，瀏覽器切換更流暢。
    return activeMainEl === mainA ? mainB : mainA
  }

  function unlock(targetUrl?: string): void {
    // iOS Safari 解鎖（per-element，flowy.fm 實戰文）：
    //   1. 解鎖是 per-element：每個會被 play() 的元素都要各別解鎖一次，沒有 page-level 效果。
    //   2. 必須在 user gesture 的 sync stack 內觸發 play()。
    //   3. promise resolve 前**絕對不要** sync pause()——會把 element 標記為 paused，
    //      iOS 認定這次解鎖無效。pause + currentTime=0 一律放 .then() 內。
    //   4. silent WAV 16 bytes、play() promise 瞬間 resolve，不會真的出聲、不會播完。
    //   5. 解鎖後 element 的 src 仍可換成真實 URL，後續 play() 終身不再被 reject。
    //
    // 解鎖目標：所有「未在播、尚未解鎖、src 為空」的元素。src 已有真實 URL 的元素跳過
    // ——unlock 的副作用（清 src、設 SILENT_WAV）會被後續 startPlayback 的 src 設值覆蓋，
    // 但 promise resolve 後跑的 src 還原動作若搶在 startPlayback 之後，會把 mainA 的
    // src 洗回 SILENT_WAV 或空，跳過 src 已有內容的元素就不會踩到這個 race。
    //
    // targetUrl 暫保留在介面：若 iOS 未來放寬規則允許對真實 URL 預解鎖，這個參數就是入口。
    void targetUrl
    for (const el of all) {
      if (!el.paused) continue
      if (unlockedSet.has(el)) continue
      if (el.src) continue
      // 設 SILENT_WAV 觸發 16-byte 解鎖。promise resolve 後才 pause，避免 race。
      el.src = SILENT_WAV
      const p = el.play()
      if (p && typeof p.then === 'function') {
        p.then(() => {
          el.pause()
          el.currentTime = 0
          // 清掉 SILENT_WAV 的 src——但只在 src 還停留 SILENT_WAV 時清。startPlayback
          // 已 sync 把 src 換成真實 URL 的情況下，這裡不要再動（避免覆蓋掉真實播放）。
          if (el.src === SILENT_WAV) {
            try { el.removeAttribute('src') } catch { el.src = '' }
          }
          unlockedSet.add(el)
        }).catch(() => {
          // 解鎖失敗（page 還沒任何 gesture、play() 被 NotAllowedError reject）：
          // 同樣只在 src 還停留 SILENT_WAV 時清。
          if (el.src === SILENT_WAV) {
            try { el.removeAttribute('src') } catch { el.src = '' }
          }
        })
      }
    }
  }

  function preload(url: string): Promise<boolean> {
    const el = pickMainEl(url)
    if (el.src.includes(url) && el.readyState >= 3) return Promise.resolve(true)
    return new Promise((resolve) => {
      const ok = () => { cleanup(); resolve(true) }
      const bad = () => { cleanup(); resolve(false) }
      const cleanup = () => {
        el.removeEventListener('canplay', ok)
        el.removeEventListener('error', bad)
      }
      el.addEventListener('canplay', ok)
      el.addEventListener('error', bad)
      el.src = url
    })
  }

  function startPlayback(args: StartPlaybackArgs, onEnded: () => void, onPlayRejected: () => void): PlaybackHandle {
    const isPreview = args.durationSec !== undefined
    const el = isPreview ? previewEl : pickMainEl(args.url)
    // 記下「目前 active 是誰」給下次接播用，pickMainEl 看到 mainA 已經在播
    // 就走 mainB，避免同一段 src 蓋上去讓播放路徑競爭。
    if (!isPreview) activeMainEl = el

    const token = Symbol('playback')
    tokens.set(el, token)
    const live = () => tokens.get(el) === token

    if (!el.src.includes(args.url)) el.src = args.url
    el.defaultPlaybackRate = args.rate
    el.playbackRate = args.rate
    el.muted = muted

    // metadata 還沒到之前設 currentTime，部分瀏覽器會吞掉——metadata 到了再補一次。
    el.currentTime = args.offsetSec
    // happy-dom 沒給 HTMLMediaElement.HAVE_METADATA 常數，hard-code 1（HTML 標準）：
    // HAVE_NOTHING=0, HAVE_METADATA=1, HAVE_CURRENT_DATA=2, HAVE_FUTURE_DATA=3, HAVE_ENOUGH_DATA=4
    if (el.readyState < 1) {
      el.addEventListener('loadedmetadata', () => { if (live()) el.currentTime = args.offsetSec }, { once: true })
    }

    el.onended = () => { if (live()) onEnded() }
    if (args.durationSec !== undefined) {
      // 試聽限長：真的出聲（playing）後才起算，才不會把載入等待時間吃進試聽長度。
      const stopAt = args.offsetSec + args.durationSec
      let timer: number | null = null
      el.addEventListener('playing', () => {
        if (!live()) return
        timer = window.setTimeout(() => {
          if (!live()) return
          el.pause()
          onEnded()
        }, Math.max(0, ((stopAt - el.currentTime) / args.rate) * 1000))
      }, { once: true })
      el.addEventListener('pause', () => { if (timer !== null) window.clearTimeout(timer) }, { once: true })
    }

    void el.play().catch(() => { if (live()) onPlayRejected() })
    const handle: PlaybackHandle = { el, globalStartSec: args.globalStartSec, offsetSec: args.offsetSec }
    handleTokens.set(handle, token)
    return handle
  }

  function positionOf(handle: PlaybackHandle): number {
    // metadata 未到時 currentTime 可能還是 0，位置不能倒退到 offset 之前。
    return handle.globalStartSec + Math.max(handle.el.currentTime, handle.offsetSec)
  }

  function stop(handle: PlaybackHandle): number {
    const pos = positionOf(handle)
    // 元素已被更新的 startPlayback 接手：這個 handle 過期了，不能動元素（會誤殺新播放）。
    if (tokens.get(handle.el) !== handleTokens.get(handle)) return pos
    tokens.set(handle.el, Symbol('stopped'))
    handle.el.onended = null
    handle.el.pause()
    if (handle.el === activeMainEl) activeMainEl = null
    return pos
  }

  function setRate(handle: PlaybackHandle, rate: number): void {
    handle.el.defaultPlaybackRate = rate
    handle.el.playbackRate = rate
  }

  function setMuted(m: boolean): void {
    muted = m
    for (const el of all) el.muted = m
  }

  return { unlock, preload, startPlayback, stop, setRate, currentPositionSec: positionOf, setMuted }
}
