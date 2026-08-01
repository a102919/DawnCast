/** 原生 <audio> 播放引擎：兩顆常駐元素——mainEl（主播放）+ previewEl（試聽，載同一
 * episode URL、音量固定調低）。
 *
 * 整集後端已合併成單一 mp3（停頓烤進音檔、cues 時間軸與音檔零誤差），前端不再需要
 * 「逐行 mp3 + mainA/mainB 輪替接播」模擬連續播放——句子切換 = `el.currentTime = cue.start`，
 * 原生 <audio> 的 ended 事件就是整集真正播完，不存在段落邊界切字問題。連帶消失的是
 * 舊架構為了模擬連續播放而生的一整組 iOS workaround：unlock 解鎖儀式＋無聲 WAV
 * （所有 play() 現在都天然落在 user gesture 內，沒有「自動接播不在 gesture 內」這件事）、
 * warmup 冷啟預熱、preload/A-B 輪替、token/handle WeakMap 世代比對。
 *
 * 仍要處理的 iOS Safari 限制：
 * 1. `<audio>` 元素必須在 DOM 內才會真的出聲，`new Audio()` 拿到的 detached 元素
 *    play() 會被瀏覽器 silent reject（見 attachToDOM）。
 * 2. metadata 還沒到（readyState < HAVE_METADATA）前設 currentTime 會被部分瀏覽器
 *    吞掉，loadedmetadata 觸發後要補設一次。用遞增的 generation 整數識別「這次補設
 *    是不是還對應目前這次 load」，load() 換集數會讓舊 generation 的補設 callback
 *    自然失效——單一 mainEl 不需要 WeakMap 做 identity 比對。
 * 3. play() 的 promise 還沒 resolve/reject 前呼叫 pause() 會被瀏覽器判定為「這次
 *    播放授權被中斷」（AbortError），實機上偶發連帶撤銷後續播放權限；pause 一律
 *    等 play() 這次呼叫落定才真的動元素（pending-play guard）。
 * 4. el.volume 在 iOS 上唯讀（靜默忽略），音量控制只能碰 muted；previewEl 建立時
 *    仍設一次較低 volume 當 best-effort（桌面有效，iOS 降級成原音量，可接受）。
 */

export const DUCK_LEVEL = 0.3

export interface MainEventHandlers {
  readonly onTimeUpdate: () => void
  readonly onSeeked: () => void
  readonly onPlay: () => void
  readonly onPause: () => void
  readonly onEnded: () => void
}

export interface AudioEngine {
  /** 換集數：重設 src 並讓 generation 前進，讓任何過期的補設 seek 失效。 */
  load(url: string): void
  play(): Promise<void>
  pause(): void
  seek(sec: number): void
  setRate(rate: number): void
  setMuted(m: boolean): void
  currentTime(): number
  duration(): number
  /** 試聽：previewEl seek 到 startSec 開始播，`playing` 事件（真的出聲，非 loading
   *  等待）後起算 durationSec 限長自動停止。不動主播放的 src / currentTime / 狀態。 */
  playClip(startSec: number, durationSec: number): void
}

/** preservesPitch 未進所有 TS lib / 瀏覽器版本，連同 webkit 前綴一起設，
 *  否則變速會變聲音（chipmunk 效果）。 */
function setPreservesPitch(el: HTMLAudioElement): void {
  const anyEl = el as HTMLAudioElement & { preservesPitch?: boolean; webkitPreservesPitch?: boolean }
  anyEl.preservesPitch = true
  anyEl.webkitPreservesPitch = true
}

/** 隱形容器：給 audio 元素一個 DOM 落腳處（iOS Safari 必要）；不影響 layout、不
 *  阻擋點擊、不被輔助技術讀到。測試環境（happy-dom/jsdom）沒 document.body 時
 *  降級成 detached（host 仍會建立，但 fake Audio 不是真 Element，不會被 append）。 */
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

export function createAudioEngine(handlers: MainEventHandlers): AudioEngine {
  const mainEl = new Audio()
  mainEl.preload = 'auto'
  setPreservesPitch(mainEl)

  const previewEl = new Audio()
  previewEl.preload = 'auto'
  previewEl.volume = DUCK_LEVEL
  setPreservesPitch(previewEl)

  attachToDOM([mainEl, previewEl])

  mainEl.addEventListener('timeupdate', handlers.onTimeUpdate)
  mainEl.addEventListener('seeked', handlers.onSeeked)
  mainEl.addEventListener('play', handlers.onPlay)
  mainEl.addEventListener('pause', handlers.onPause)
  mainEl.addEventListener('ended', handlers.onEnded)

  /** load() 換集數就前進一格；補設 seek 的 loadedmetadata callback 憑這個數字判斷
   *  自己是不是還對應「目前這次」load，過期（使用者已經切了下一集）就不執行。 */
  let generation = 0
  /** play() 呼叫中、promise 尚未落定：pause() 必須等它落定才真的動元素（見檔頭 3）。 */
  let pendingPlay: Promise<void> | null = null
  let previewStopTimer: number | null = null
  /** 上一次 playClip 掛的 once('playing') listener：若它還沒觸發就被新的試聽取代，
   *  必須先拆掉，否則它會在新試聽的 playing 事件一起開火，用舊 stopAt 提早停掉新試聽。 */
  let previewPlayingListener: (() => void) | null = null

  /** metadata 還沒到就設 currentTime 可能被瀏覽器吞掉，loadedmetadata 後補設一次。
   *  mainEl 補設要看 generation 有沒有過期；previewEl 是短生命週期的試聽動作，
   *  不共用 mainEl 的世代（切換主集數不該打斷正在放的試聽）。 */
  function seekWithMetadataGuard(el: HTMLAudioElement, sec: number): void {
    el.currentTime = sec
    if (el.readyState < 1) {
      const genAtCall = generation
      el.addEventListener('loadedmetadata', () => {
        if (el === mainEl && genAtCall !== generation) return
        el.currentTime = sec
      }, { once: true })
    }
  }

  function load(url: string): void {
    generation++
    mainEl.src = url
    // previewEl 載同一個 episode URL（試聽是對整集檔內任意區段 seek 抽樣）；
    // 同 URL 走 HTTP cache，不會重拉整檔。換集時順手停掉還在放的舊集試聽。
    if (previewStopTimer !== null) { window.clearTimeout(previewStopTimer); previewStopTimer = null }
    previewEl.pause()
    previewEl.src = url
  }

  function play(): Promise<void> {
    const p = mainEl.play()
    pendingPlay = p
    void p.catch(() => undefined).finally(() => { if (pendingPlay === p) pendingPlay = null })
    return p
  }

  function pause(): void {
    if (pendingPlay) {
      const inFlight = pendingPlay
      void inFlight.then(() => mainEl.pause()).catch(() => undefined)
      return
    }
    mainEl.pause()
  }

  function setRate(rate: number): void {
    mainEl.defaultPlaybackRate = rate
    mainEl.playbackRate = rate
    previewEl.defaultPlaybackRate = rate
    previewEl.playbackRate = rate
  }

  function setMuted(m: boolean): void {
    mainEl.muted = m
    previewEl.muted = m
  }

  function playClip(startSec: number, durationSec: number): void {
    if (previewStopTimer !== null) { window.clearTimeout(previewStopTimer); previewStopTimer = null }
    if (previewPlayingListener !== null) {
      previewEl.removeEventListener('playing', previewPlayingListener)
    }
    previewEl.pause()
    seekWithMetadataGuard(previewEl, startSec)
    const stopAt = startSec + durationSec
    const onPlaying = (): void => {
      previewPlayingListener = null
      const rate = previewEl.playbackRate || 1
      const remainMs = Math.max(0, ((stopAt - previewEl.currentTime) / rate) * 1000)
      previewStopTimer = window.setTimeout(() => { previewEl.pause() }, remainMs)
    }
    previewPlayingListener = onPlaying
    previewEl.addEventListener('playing', onPlaying, { once: true })
    void previewEl.play().catch(() => undefined)
  }

  return {
    load,
    play,
    pause,
    seek: (sec: number) => seekWithMetadataGuard(mainEl, sec),
    setRate,
    setMuted,
    currentTime: () => mainEl.currentTime,
    duration: () => mainEl.duration,
    playClip,
  }
}
