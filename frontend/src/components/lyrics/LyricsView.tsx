import { useEffect, useMemo, useRef, useState } from 'react'
import { useReducedMotion } from 'framer-motion'
import { Repeat1, Copy } from 'lucide-react'
import { toast } from 'sonner'
import type { Cue, SourceReference } from '../../types/episode'
import { useVocab } from '../../state'
import { findActiveCueIndex, splitTextToWords, getCoverArt, coverArtBackground } from '../../lib'
import { renderTokenized } from '../shared/renderTokenized'
import { EpisodeCover } from '../shared/EpisodeCover'
import { EpisodeReferences } from '../player/EpisodeReferences'

const RESUME_AUTOSCROLL_MS = 3000

interface LyricsViewProps {
  readonly episodeId: string
  readonly episodeTitle: string
  readonly cues: readonly Cue[]
  readonly currentTime: number
  readonly onWordClick: (word: string, cue: Cue) => void
  readonly onCueClick?: (cue: Cue) => void
  /** 點字時嘗試 seek 到該字時間點。回 true 表示已 seek，false 表示沒 word
   *  boundary（fallback 由 LyricsView 決定是否改走 cue-level click）。
   *  沒傳就不做任何額外處理（沿用既有「點字只查詞」行為）。 */
  readonly onWordSeek?: (word: string, cue: Cue) => boolean
  /** 行動版參考資料浮動卡；桌面版由 PlayerRoute footer 另渲一份 */
  readonly references?: readonly SourceReference[]
  /** 重複聽的練習開關：關掉的那一層改成模糊佔位，點該行可單獨揭曉。
   *  預設全開＝原本的雙語顯示（PracticeRoute 不傳就是現況）。 */
  readonly showEn?: boolean
  readonly showZh?: boolean
  /** 當句操作列的單句循環；沒傳 onCueLoopToggle 就不渲染循環鈕（只留複製）。 */
  readonly isCueLooping?: boolean
  readonly onCueLoopToggle?: () => void
}

/** 當句操作列：只掛在正在播的那一句底下。刻意渲染在 role="button" 那層的**外面**
 *  （見下方 wrapper），button 巢在 role="button" 裡是無效的互動巢狀，拉出來同時
 *  省掉整排 stopPropagation。 */
function CueActions({ cue, isCueLooping, onCueLoopToggle }: {
  readonly cue: Cue
  readonly isCueLooping?: boolean
  readonly onCueLoopToggle?: () => void
}) {
  // 純圖示鈕：36px 方形觸控目標，語意只靠 aria-label（下方兩顆都有）。
  const pill = 'inline-flex items-center justify-center w-9 h-9 rounded-full border border-border text-text-secondary hover:text-text-primary hover:bg-bg-secondary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent'

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(cue.text)
      toast.success('已複製句子')
    } catch (err) {
      console.error(err)
      toast.error('複製失敗，請重試')
    }
  }

  return (
    <div className="flex items-center gap-2 mt-2">
      {onCueLoopToggle && (
        <button
          type="button"
          onClick={onCueLoopToggle}
          aria-pressed={isCueLooping}
          aria-label={isCueLooping ? '關閉單句循環' : '循環播放這句'}
          className={`${pill} ${isCueLooping ? 'text-accent bg-accent/10 border-accent/40' : ''}`}
        >
          <Repeat1 size={16} />
        </button>
      )}
      <button type="button" onClick={() => void handleCopy()} aria-label="複製這句英文" className={pill}>
        <Copy size={16} />
      </button>
    </div>
  )
}

/** Apple Music 風大歌詞：當句大字置中、上下句半透、自動捲入中央；點字同樣接查詞。
 *
 * 設計要點：
 * - 三句可見（前一句 / 當句 / 後一句）；active 句左右 padding 大、字級 2xl、白色，
 *   其他句字級 base、半透明，與 Apple Music 一致。
 * - 自動捲：activeCueIdx 變動時 scrollIntoView({ block: 'center' })。使用者手動滾動
 *   （wheel / touchmove）時暫停自動捲動，停手 3 秒後自動恢復跟隨當句。
 */
export function LyricsView({
  episodeId, episodeTitle, cues, currentTime, onWordClick, onCueClick, onWordSeek, references,
  showEn = true, showZh = true, isCueLooping, onCueLoopToggle,
}: LyricsViewProps) {
  const { isInVocab } = useVocab()
  const art = useMemo(() => getCoverArt(episodeId), [episodeId])
  const activeCueIdx = useMemo(() => findActiveCueIndex(cues, currentTime), [cues, currentTime])
  const activeRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const isUserScrollingRef = useRef(false)
  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const reduceMotion = useReducedMotion()
  /** 被點開的那一行（隱藏模式下的臨時揭曉）。連同「揭曉當下的當句」一起記，
   *  播到下一句時 at 不再相符就自動失效——不必用 effect 去清，省一輪 cascading render。 */
  const [revealed, setRevealed] = useState<{ readonly idx: number; readonly at: number } | null>(null)
  const revealedIdx = revealed !== null && revealed.at === activeCueIdx ? revealed.idx : null

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const handleUserScroll = () => {
      isUserScrollingRef.current = true
      clearTimeout(resumeTimerRef.current)
      resumeTimerRef.current = setTimeout(() => {
        isUserScrollingRef.current = false
      }, RESUME_AUTOSCROLL_MS)
    }
    container.addEventListener('wheel', handleUserScroll, { passive: true })
    container.addEventListener('touchmove', handleUserScroll, { passive: true })
    return () => {
      container.removeEventListener('wheel', handleUserScroll)
      container.removeEventListener('touchmove', handleUserScroll)
      clearTimeout(resumeTimerRef.current)
    }
  }, [])

  useEffect(() => {
    if (activeCueIdx < 0 || isUserScrollingRef.current) return
    activeRef.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' })
  }, [activeCueIdx, reduceMotion])

  return (
    <div className="relative h-full">
      {/* 封面藝術模糊背景（不隨捲動移動，比照 Apple Music 播放頁做法） */}
      <div
        className="absolute inset-0 opacity-35 blur-3xl scale-125 pointer-events-none"
        style={{ background: coverArtBackground(art) }}
      />
      <div className="absolute inset-0 lyrics-scrim pointer-events-none" />
      <div
        ref={containerRef}
        className="relative z-10 h-full overflow-y-auto px-6 pb-[100px] lg:pb-40"
        aria-label="歌詞"
      >
        {/* Cover + title：當成第一個 scroll item，與歌詞一起滾動 */}
        <div className="flex items-center gap-4 pt-6 lg:pt-8 pb-4 shrink-0">
          <EpisodeCover episodeId={episodeId} size="lg" />
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-widest text-text-tertiary mb-1">
              Podcast
            </div>
            <h1 className="text-base lg:text-lg font-semibold truncate">{episodeTitle}</h1>
          </div>
        </div>

        <div className="space-y-6 md:space-y-10">
          {cues.map((cue, i) => {
            const isActive = i === activeCueIdx
            const revealed = revealedIdx === i
            const enHidden = !showEn && !revealed
            const zhHidden = !showZh && !revealed
            const tokens = splitTextToWords(cue.text)
            // 模糊層是「點一下揭曉」的唯一點擊目標，所以要擋住冒泡到外層的跳句。
            const reveal = (e: { stopPropagation: () => void }) => {
              e.stopPropagation()
              setRevealed({ idx: i, at: activeCueIdx })
            }
            const handleWord = (word: string) => {
              // 練習模式 word seek：有 onWordSeek handler 優先叫（PlayerRoute 會在
              // 內部決定走 word-level 還是 cue-level fallback）。
              if (onWordSeek && onWordSeek(word, cue)) return
              // 沒有 word seek handler 或 seek 拒絕（沒 word boundary）：走原本的查詞。
              onWordClick(word, cue)
            }
            return (
              // 外層 wrapper 不可互動：操作列的 <button> 巢在 role="button" 裡是無效的
              // 互動巢狀，所以拆成兄弟節點。activeRef 掛這層，scrollIntoView 才會把
              // 操作列一起算進置中範圍。
              <div key={cue.index} ref={isActive ? activeRef : undefined}>
                <div
                  role="button"
                  tabIndex={0}
                  aria-label={`跳到 ${cue.speaker}: ${cue.text}`}
                  className="cursor-pointer rounded-lg px-2 py-1 -mx-2 transition-all duration-300 ease-apple hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
                  onClick={() => onCueClick?.(cue)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      onCueClick?.(cue)
                    }
                  }}
                >
                  {/* speaker 標籤（小字） */}
                  <div
                    className={`text-xs font-medium uppercase tracking-wider mb-1 transition-opacity duration-300 ${
                      isActive ? 'opacity-60 text-text-tertiary' : 'opacity-40 text-text-tertiary'
                    }`}
                  >
                    {cue.speaker}
                  </div>

                  {/* 英文主歌詞。隱藏時故意退回純文字不跑 renderTokenized——模糊層底下
                      若還留著可點的 word span，「點一下揭曉」會先打到某個字去查詞。
                      ponytail: 模糊行沒有鍵盤／AT 揭曉路徑（再塞 focusable 就繞回無效
                      巢狀），鍵盤使用者用工具列上真正的 中/EN 開關切回來，是等價路徑。 */}
                  <p
                    className={`leading-relaxed mb-1 ${
                      isActive
                        ? 'text-2xl md:text-3xl font-semibold text-text-primary'
                        : 'text-base text-text-tertiary opacity-60'
                    }${enHidden ? ' blur-sm select-none' : ''}`}
                    onClick={enHidden ? reveal : undefined}
                  >
                    {enHidden ? cue.text : renderTokenized(
                      cue.text,
                      tokens,
                      handleWord,
                      isInVocab,
                      { stopPropagation: true, nonVocabHoverClass: 'hover:bg-bg-secondary/60' },
                    )}
                  </p>

                  {/* 中文翻譯 */}
                  <p
                    className={`leading-relaxed ${
                      isActive
                        ? 'text-base text-accent/80'
                        : 'text-sm text-text-tertiary opacity-60'
                    }${zhHidden ? ' blur-sm select-none' : ''}`}
                    onClick={zhHidden ? reveal : undefined}
                  >
                    {cue.zh}
                  </p>
                </div>

                {isActive && (
                  <CueActions cue={cue} isCueLooping={isCueLooping} onCueLoopToggle={onCueLoopToggle} />
                )}
              </div>
            )
          })}

          {/* 行動版參考資料：與台詞同一層，desktop 由 footer 另渲一份避免重複。 */}
          {references && references.length > 0 && (
            <div className="lg:hidden">
              <EpisodeReferences references={references} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
