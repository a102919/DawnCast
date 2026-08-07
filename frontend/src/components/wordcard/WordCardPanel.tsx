import { useState, type ReactNode } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X, BookmarkPlus, Check, Loader2, AlertCircle, RotateCcw, Play, ExternalLink } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '../../api'
import type { DictEntry } from '../../api/types'
import type { Cue } from '../../types/episode'
import { usePlayer, useVocab } from '../../state'
import { formatPos, formatTimestamp, formatMultiline, findActiveCueIndex } from '../../lib'
import { useSprings } from '../../lib/motion'
import { IconButton, Sheet } from '../primitives'
import { MnemonicHint } from './MnemonicHint'
import { PronounceButton } from './PronounceButton'

function addVocabButtonTone(inVocab: boolean, hasEntry: boolean): string {
  if (inVocab) return 'bg-success/10 text-success cursor-default'
  if (!hasEntry) return 'bg-bg-secondary text-text-tertiary cursor-default'
  return 'bg-accent text-white hover:bg-accent-hover cursor-pointer'
}

function highlightWord(sentence: string, word: string): ReactNode {
  if (!word) return sentence
  const lower = sentence.toLowerCase()
  const lowerWord = word.toLowerCase()
  const idx = lower.indexOf(lowerWord)
  if (idx === -1) return sentence
  return (
    <>
      {sentence.slice(0, idx)}
      <span className="font-semibold text-accent">{sentence.slice(idx, idx + word.length)}</span>
      {sentence.slice(idx + word.length)}
    </>
  )
}

/** 加入單字本按鈕的視覺狀態：依 isPending/inVocab 查表決定 icon 與文案 */
interface AddVocabButtonState {
  readonly key: string
  readonly when: boolean
  readonly icon: ReactNode
  readonly label: string
}

interface WordCardPanelProps {
  readonly isOpen: boolean
  readonly word: string | null
  readonly entry: DictEntry | null
  readonly lookupError: string | null
  readonly onRetry: () => void
  readonly activeCue: Cue | null
  readonly episodeId: string
  readonly activeCueIdx: number
  readonly onClose: () => void
  /** 提供時顯示「前往該集」按鈕（單字本情境：跳到來源集數的播放頁） */
  readonly onGoToSource?: () => void
}

export function WordCardPanel({ isOpen, word, entry, lookupError, onRetry, activeCue, episodeId, activeCueIdx, onClose, onGoToSource }: WordCardPanelProps) {
  const player = usePlayer()
  const { addVocab, isInVocab } = useVocab()
  const { snappy } = useSprings()
  const inVocab = entry ? isInVocab(entry.word) : false
  const [isPending, setIsPending] = useState(false)

  const addVocabButtonStates: AddVocabButtonState[] = [
    { key: 'pending', when: isPending, icon: <Loader2 size={14} className="animate-spin" />, label: '加入中' },
    { key: 'added', when: !isPending && inVocab, icon: <Check size={14} />, label: '已收錄' },
    { key: 'idle', when: !isPending && !inVocab, icon: <BookmarkPlus size={14} />, label: '加入單字本' },
  ]
  const addVocabButtonState =
    addVocabButtonStates.find((state) => state.when) ?? addVocabButtonStates[addVocabButtonStates.length - 1]

  // 就地重播該句原音：player 沒載該集就補抓（單字本情境），再用試聽元素
  // playClip 播該句的 [cue.start, cue.end) 片段。詞卡不關、主播放進度不動。
  const handleReplayCue = async () => {
    if (!activeCue) return
    try {
      let ep = player.currentEpisode
      if (!ep || ep.id !== episodeId) {
        ep = await api.getEpisode(episodeId)
        player.setCurrentEpisode(ep)
      }
      // 舊集 / 尚未 backfill 完成可能沒有整集音檔，靜默不播體感像壞掉，給明確回饋
      if (!ep.audioUrl) {
        toast('這集沒有原音檔，無法重聽')
        return
      }
      // 單字本的 sourceTimestamp 經 DB float4 捨入常比 cue.start 小一點點，用時間戳
      // 反查會二分搜尋掉到前一句（播出來跟顯示句對不上）。activeCueIdx 是收錄當下的
      // cue 陣列索引（精確整數），優先用它；越界（舊資料）才退回時間戳查找。
      const idx = activeCueIdx >= 0 && activeCueIdx < ep.cues.length
        ? activeCueIdx
        : Math.max(0, findActiveCueIndex(ep.cues, activeCue.start))
      const cue = ep.cues[idx]
      if (!cue) return
      player.playClip(cue.start, cue.end - cue.start)
    } catch {
      toast.error('載入原音失敗，請重試')
    }
  }

  const handleAddVocab = async () => {
    if (!word || !entry || !activeCue) return
    setIsPending(true)
    try {
      await addVocab({
        word,
        lemma: entry.word,
        pos: entry.pos[0],
        translation: entry.translation,
        ipa: entry.ipa,
        sourceEpisodeId: episodeId,
        sourceLineNo: activeCueIdx,
        sourceTimestamp: activeCue.start,
        sourceSentence: activeCue.text,
        sourceSentenceZh: activeCue.zh,
        senseIdx: 0,
      })
    } catch {
      toast.error('加入單字本失敗，請重試')
    } finally {
      setIsPending(false)
    }
  }

  // 關閉詞卡：「重聽這句」走試聽元素（playClip），不算進全域 isPlaying，沒人主動
  // 叫停就會留在背景播完整句。player.pause() 只動主播放元素，對試聽是安全 no-op，
  // 一律先呼叫它再交還 onClose，呼叫端（useWordLookup.close）之後照舊決定要不要
  // 恢復原本被蓋掉的主播放。
  const handleClose = () => {
    player.pause()
    onClose()
  }

  return (
    <Sheet isOpen={isOpen} onClose={handleClose} variant="bottom" ariaLabelledBy="word-card-panel-title" maxHeight="90vh">
      <div className="px-5 pb-6 overflow-y-auto max-h-[calc(90vh-40px)]">
        {/* 標題列 */}
        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="flex items-center gap-2">
              <h3 id="word-card-panel-title" className="text-2xl font-semibold text-text-primary">
                {word ?? '—'}
              </h3>
              {/* 單字發音走字典音檔／TTS；從節目 mp3 猜偏移抽樣不準（無逐字時間戳），已棄用 */}
              {entry && <PronounceButton audioUrl={entry.audioUrl} text={word} size={20} />}
            </div>
            {entry && entry.pos.length > 0 && (
              <div className="flex items-center gap-2 mt-0.5">
                <span className="text-xs text-text-tertiary">{formatPos(entry.pos)}</span>
              </div>
            )}
            {!entry && word && !lookupError && (
              <div className="flex items-center gap-2 mt-0.5 animate-pulse">
                <div className="h-3.5 bg-bg-secondary rounded w-20" />
                <div className="h-3.5 bg-bg-secondary rounded w-10" />
              </div>
            )}
          </div>
          <div className="flex items-center gap-1 mt-1">
            <motion.button
              layout
              onClick={handleAddVocab}
              disabled={inVocab || !entry || isPending}
              transition={snappy}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium overflow-hidden transition-[background-color,color,transform] duration-fast ease-apple active:scale-[0.97] disabled:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${addVocabButtonTone(inVocab, !!entry)}`}
            >
              <AnimatePresence mode="wait" initial={false}>
                <motion.span
                  key={addVocabButtonState.key}
                  initial={{ opacity: 0, scale: 0.7 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.7 }}
                  transition={snappy}
                  className="inline-flex items-center gap-1.5"
                >
                  {addVocabButtonState.icon} {addVocabButtonState.label}
                </motion.span>
              </AnimatePresence>
            </motion.button>
            <IconButton label="關閉詞卡" onClick={handleClose}>
              <X size={18} />
            </IconButton>
          </div>
        </div>

        <hr className="border-border mb-3" />

        {/* 內容 */}
        {lookupError ? (
          <div className="flex flex-col items-center gap-2 py-3">
            <AlertCircle size={20} className="text-danger" />
            <p className="text-sm text-danger">{lookupError}</p>
            <button
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-text-secondary bg-bg-secondary hover:bg-border rounded-md transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              onClick={onRetry}
            >
              <RotateCcw size={13} />
              重試
            </button>
          </div>
        ) : !entry && word && !lookupError ? (
          <div className="space-y-2.5 animate-pulse">
            {/* 翻譯 skeleton */}
            <div className="h-6 bg-bg-secondary rounded w-3/4" />
            {/* IPA + 詞性 skeleton */}
            <div className="flex gap-2">
              <div className="h-4 bg-bg-secondary rounded w-24" />
              <div className="h-4 bg-bg-secondary rounded w-12" />
            </div>
            {/* 來源 skeleton */}
            <div className="h-3 bg-bg-secondary rounded w-40 mt-1" />
          </div>
        ) : !entry ? (
          <p className="text-text-secondary text-sm">找不到釋義</p>
        ) : (
          <div className="space-y-3">
            {/* 主義項：senses[0] 精煉過的字才有；其餘 180 萬列尚未精煉，退回舊 translation 渲染 */}
            {entry.senses && entry.senses.length > 0 ? (
              <div className="flex items-baseline gap-2 flex-wrap">
                {entry.senses[0].pos && (
                  <span className="shrink-0 text-xs font-mono text-text-tertiary border border-border px-1.5 py-0.5 rounded">
                    {entry.senses[0].pos}
                  </span>
                )}
                <p className="text-xl font-medium text-text-primary">{entry.senses[0].zh}</p>
              </div>
            ) : (
              <p className="text-xl font-medium text-text-primary whitespace-pre-line">{formatMultiline(entry.translation)}</p>
            )}

            {/* podcast 語境句：優先序最高，只要這句話有播出就顯示，不再要求字典先有例句 */}
            {activeCue && (
              <div className="mt-1 space-y-2">
                <div className="border-l-2 border-accent pl-3 py-1">
                  <p className="text-base leading-relaxed text-text-primary">
                    {highlightWord(activeCue.text, word ?? '')}
                  </p>
                </div>
                <div className="flex items-center gap-4">
                  <button
                    type="button"
                    onClick={() => void handleReplayCue()}
                    className="inline-flex items-center gap-1.5 text-xs font-medium text-accent hover:text-accent-hover transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                  >
                    <Play size={12} fill="currentColor" />
                    重聽這句
                  </button>
                  {onGoToSource && (
                    <button
                      type="button"
                      onClick={onGoToSource}
                      className="inline-flex items-center gap-1.5 text-xs font-medium text-text-tertiary hover:text-accent transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                    >
                      <ExternalLink size={12} />
                      前往該集
                    </button>
                  )}
                </div>
                <p className="text-xs text-text-tertiary">
                  來自 {formatTimestamp(activeCue.start)} · {activeCue.speaker}
                </p>
              </div>
            )}

            {/* 典型用法：字典自帶例句，跟 podcast 語境句互相獨立，兩者可同時存在也可各自缺席 */}
            {(entry.exampleEn || entry.exampleZh) && (
              <div className="border-l-2 border-border-strong pl-3 py-1 space-y-1">
                <p className="text-xs font-mono text-text-tertiary">典型用法</p>
                {entry.exampleEn && (
                  <p className="text-sm leading-relaxed text-text-primary flex items-start gap-1.5">
                    <span>{entry.exampleEn}</span>
                    <PronounceButton audioUrl={null} text={entry.exampleEn} size={16} label="播放例句發音" />
                  </p>
                )}
                {entry.exampleZh && (
                  <p className="text-sm leading-relaxed text-text-secondary">
                    {entry.exampleZh}
                  </p>
                )}
              </div>
            )}

            {/* 核心語意：只有精煉過的字才有 */}
            {entry.coreSense && (
              <div className="bg-bg-secondary rounded-md px-3 py-2 flex items-start gap-2">
                <span className="shrink-0 text-xs text-text-tertiary">💡 核心語意</span>
                <span className="text-sm text-text-secondary">{entry.coreSense}</span>
              </div>
            )}

            {/* 其他語意：收合，只在有第二個以上義項時出現 */}
            {entry.senses && entry.senses.length > 1 && (
              <details>
                <summary className="text-xs font-mono text-text-tertiary cursor-pointer select-none hover:text-text-secondary transition-colors duration-fast">
                  其他語意（{entry.senses.length - 1}）
                </summary>
                <div className="mt-2 space-y-1.5">
                  {entry.senses.slice(1).map((sense, i) => (
                    <div key={i} className="flex items-baseline gap-2">
                      {sense.pos && (
                        <span className="shrink-0 text-xs font-mono text-text-tertiary border border-border px-1.5 py-0.5 rounded">
                          {sense.pos}
                        </span>
                      )}
                      <span className="text-sm text-text-secondary">{sense.zh}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {entry.mnemonic && (
              <div className="pt-1">
                <MnemonicHint text={entry.mnemonic} />
              </div>
            )}
          </div>
        )}
      </div>
    </Sheet>
  )
}
