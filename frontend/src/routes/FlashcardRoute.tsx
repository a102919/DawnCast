import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion, AnimatePresence, useMotionValue } from 'framer-motion'
import { Sparkles, ArrowLeft, Frown, Meh, Smile, SmilePlus, BookOpen, CalendarCheck, RotateCw } from 'lucide-react'
import { useVocab } from '../state'
import type { VocabItem } from '../api/types'
import { EmptyState } from '../components/primitives/EmptyState'
import { StatCard } from '../components/primitives/StatCard'
import { Chip } from '../components/primitives/Chip'
import { Button } from '../components/primitives/Button'
import { ClozeCard } from '../components/flashcard/ClozeCard'
import { ReplayAudioButton } from '../components/flashcard/ReplayAudioButton'
import { MnemonicHint } from '../components/wordcard/MnemonicHint'
import { PronounceButton } from '../components/wordcard/PronounceButton'
import { useSprings } from '../lib/motion'
import { filterDueDeck, buildCloze, storageGet, storageSet, formatMultiline } from '../lib'

type Phase = 'answer' | 'result'
type Mode = 'recognize' | 'cloze'
type Direction = 'advance' | 'switch'

const MODE_STORAGE_KEY = 'dawncast:flashcards:mode'

function clozeSentence(item: VocabItem): string {
  return item.sourceSentence || item.exampleEn || ''
}

/** 'advance'：真的前進一張（含跳到結算頁），左右滑動；'switch'：同一張卡切換辨識/拼字模式，只淡入淡出。
 * custom 要同時掛在 <AnimatePresence> 本身（給退場中的舊子層）和每個子 motion.div（給掛載中的新子層）。 */
const cardVariants = {
  enter: (dir: Direction) => (dir === 'advance' ? { opacity: 0, x: 24 } : { opacity: 0 }),
  center: { opacity: 1, x: 0 },
  exit: (dir: Direction) => (dir === 'advance' ? { opacity: 0, x: -24 } : { opacity: 0 }),
}

export function FlashcardRoute() {
  const { items, updateCardReview } = useVocab()
  const navigate = useNavigate()
  const { gentle, snappy, press, reduce: shouldReduceMotion } = useSprings()
  const cardScale = useMotionValue(1)
  const shadowPeak = useMotionValue(0)

  const [deck] = useState<readonly VocabItem[]>(() => filterDueDeck(items))
  const [idx, setIdx] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [results, setResults] = useState<number[]>([])
  const [phase, setPhase] = useState<Phase>('answer')
  const [mode, setMode] = useState<Mode>(() => storageGet<Mode>(MODE_STORAGE_KEY) ?? 'recognize')
  const [direction, setDirection] = useState<Direction>('switch')

  const current = deck[idx]
  const sentence = current ? clozeSentence(current) : ''
  const canCloze = current ? buildCloze(sentence, current.word) !== null : false
  const effectiveMode: Mode = mode === 'cloze' && canCloze ? 'cloze' : 'recognize'

  const changeMode = (next: Mode) => {
    setDirection('switch')
    setMode(next)
    storageSet(MODE_STORAGE_KEY, next)
  }

  const answer = (quality: number) => {
    if (!current) return
    setDirection('advance')
    // 樂觀更新：先前進畫面給使用者回饋，背景同步失敗時整批撤回並提示重試，
    // 否則卡片已翻頁、評分已記，伺服器沒收到會讓 SRS 排程漂掉。
    const capturedIdx = idx
    setResults(r => [...r, quality])
    setFlipped(false)
    const next = idx + 1
    setIdx(next)
    if (next >= deck.length) setPhase('result')
    void updateCardReview(current.id, quality).catch((err: unknown) => {
      setIdx(capturedIdx)
      setResults(r => r.slice(0, -1))
      setPhase('answer')
      setDirection('switch')
      window.alert(
        `同步評分失敗（${err instanceof Error ? err.message : '未知錯誤'}），已退回本卡，請重試`,
      )
    })
  }

  if (items.length === 0) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-6">
        <button
          onClick={() => navigate('/vocab')}
          className="inline-flex items-center gap-1 min-h-[44px] -ml-2 px-2 mb-4 rounded-md text-body tracking-body leading-body text-text-tertiary hover:text-text-secondary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <ArrowLeft size={16} />
          回到單字本
        </button>
        <EmptyState
          icon={BookOpen}
          title="單字本是空的"
          description="先到播放頁收錄幾個單字，再來這裡練習"
          action={{ label: '去播放頁收錄', to: '/player' }}
        />
      </div>
    )
  }

  if (deck.length === 0) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-6">
        <button
          onClick={() => navigate('/vocab')}
          className="inline-flex items-center gap-1 min-h-[44px] -ml-2 px-2 mb-4 rounded-md text-body tracking-body leading-body text-text-tertiary hover:text-text-secondary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <ArrowLeft size={16} />
          回到單字本
        </button>
        <EmptyState icon={CalendarCheck} title="今天沒有到期的卡片" description="表現很好！明天繼續複習" />
      </div>
    )
  }

  const forgotten = results.filter(q => q < 3).length
  const shaky = results.filter(q => q === 3).length
  const remembered = results.filter(q => q >= 4).length

  return (
    <div className="max-w-2xl mx-auto px-4 py-6 overflow-x-hidden">
      <div className="flex items-center justify-between mb-3">
        <button
          onClick={() => navigate('/vocab')}
          className="inline-flex items-center gap-1 min-h-[44px] -ml-2 px-2 rounded-md text-body tracking-body leading-body text-text-tertiary hover:text-text-secondary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <ArrowLeft size={16} />
          回到單字本
        </button>
        <p className="text-caption tracking-caption leading-caption text-text-tertiary tabular-nums">
          {phase === 'result' ? '今日複習完成' : `第 ${idx + 1} / ${deck.length} 張`}
        </p>
      </div>

      {phase === 'answer' && (
        <>
          <div className="h-1 rounded-full bg-border overflow-hidden mb-4">
            <motion.div
              className="h-full rounded-full bg-accent origin-left"
              initial={false}
              animate={{ scaleX: idx / deck.length }}
              transition={snappy}
            />
          </div>

          <div className="mb-6">
            <div className="inline-flex gap-1 p-1 rounded-pill bg-bg-canvas">
              <Chip
                active={effectiveMode === 'recognize'}
                onClick={() => changeMode('recognize')}
                className={effectiveMode === 'recognize' ? 'shadow-sm' : ''}
              >
                辨識
              </Chip>
              <Chip
                active={effectiveMode === 'cloze'}
                onClick={() => canCloze && changeMode('cloze')}
                className={`${effectiveMode === 'cloze' ? 'shadow-sm' : ''} ${canCloze ? '' : 'opacity-40 cursor-not-allowed'}`}
              >
                拼字
              </Chip>
            </div>
            {!canCloze && (
              <p className="mt-1.5 text-caption tracking-caption leading-caption text-text-tertiary whitespace-nowrap">
                此卡無例句可挖空，僅能用辨識模式
              </p>
            )}
          </div>
        </>
      )}

      <AnimatePresence mode="wait" custom={direction}>
        {phase === 'result' ? (
          <motion.div
            key="result"
            custom={direction}
            variants={cardVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={gentle}
            className="rounded-xl border border-border/30 material-regular shadow-lg p-8 text-center space-y-5"
          >
            <motion.div
              initial={{ scale: 0.85, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ ...gentle, delay: 0.1 }}
              className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-accent/10 text-accent"
            >
              <Sparkles size={24} />
            </motion.div>
            <h2 className="text-title tracking-title leading-title font-semibold text-text-primary">
              {forgotten === 0 ? '全部記得！太強了' : '本輪複習完成'}
            </h2>
            <div className="grid grid-cols-3 gap-3 max-w-sm mx-auto">
              {[
                { label: '忘記了', value: forgotten, tone: 'danger' as const, delay: 0 },
                { label: '有點難', value: shaky, tone: 'warning' as const, delay: 0.05 },
                { label: '記得', value: remembered, tone: 'success' as const, delay: 0.1 },
              ].map(s => (
                <motion.div
                  key={s.label}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ ...gentle, delay: s.delay }}
                >
                  <StatCard label={s.label} value={s.value} tone={s.tone} />
                </motion.div>
              ))}
            </div>
            {forgotten > 0 && (
              <p className="text-body tracking-body leading-body text-text-secondary">{forgotten} 個忘記的明天再複習</p>
            )}
            <Button variant="secondary" onClick={() => navigate('/vocab')}>
              回到單字本
            </Button>
          </motion.div>
        ) : current && effectiveMode === 'cloze' ? (
          <motion.div
            key={`cloze-${idx}`}
            custom={direction}
            variants={cardVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={gentle}
          >
            <ClozeCard key={current.id} item={current} sentence={sentence} onGraded={answer} />
          </motion.div>
        ) : current ? (
          <motion.div
            key={`card-${idx}`}
            custom={direction}
            variants={cardVariants}
            initial="enter"
            animate="center"
            exit="exit"
            transition={gentle}
            className="space-y-5"
          >
            {/* 用 motion.div+role="button" 而非 <button>：背面放了 ReplayAudioButton/MnemonicHint
                兩個真的互動元件，HTML 不允許 button 巢狀 button。 */}
            <motion.div
              role="button"
              tabIndex={0}
              onClick={() => setFlipped(f => !f)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setFlipped(f => !f)
                }
              }}
              aria-label={flipped ? '顯示單字面' : '顯示翻譯面'}
              whileTap={{ scale: 0.98 }}
              transition={press}
              className="relative block w-full min-h-[260px] text-left [perspective:1600px] rounded-xl cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {/* 峰值陰影層：不參與 3D 旋轉，翻轉經過 90° 瞬間淡入淡出，模擬卡片離開桌面的光影變化 */}
              {!shouldReduceMotion && (
                <motion.div
                  aria-hidden
                  className="absolute inset-0 rounded-xl shadow-lg pointer-events-none"
                  style={{ opacity: shadowPeak }}
                />
              )}

              <motion.div
                className="relative w-full h-full min-h-[260px] [transform-style:preserve-3d]"
                style={shouldReduceMotion ? undefined : { scale: cardScale }}
                animate={{ rotateY: flipped ? 180 : 0 }}
                transition={gentle}
                onUpdate={
                  shouldReduceMotion
                    ? undefined
                    : latest => {
                        const angle = typeof latest.rotateY === 'number' ? latest.rotateY : flipped ? 180 : 0
                        const t = Math.sin((angle * Math.PI) / 180)
                        cardScale.set(1 - 0.05 * t)
                        shadowPeak.set(t)
                      }
                }
              >
                <div className="absolute inset-0 [backface-visibility:hidden] rounded-xl border border-border/30 material-regular shadow-md p-7 text-center hover:border-accent/40 transition-colors duration-fast ease-apple">
                  <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase mb-3">單字</p>
                  <div className="flex items-center justify-center gap-2">
                    <p className="text-display tracking-display leading-display font-bold text-text-primary break-all">{current.word}</p>
                    <PronounceButton audioUrl={null} text={current.word} size={20} label="播放單字發音" />
                  </div>
                  {current.ipa && (
                    <p className="text-body tracking-body leading-body text-text-tertiary font-mono mt-2">{current.ipa}</p>
                  )}
                  <p className="inline-flex items-center gap-1 mt-6 text-caption tracking-caption leading-caption text-text-tertiary">
                    <RotateCw size={12} />
                    點擊卡片查看翻譯
                  </p>
                </div>

                <div className="absolute inset-0 [backface-visibility:hidden] [transform:rotateY(180deg)] rounded-xl border border-border/30 material-regular shadow-md p-7 text-left space-y-4 overflow-y-auto hover:border-accent/40 transition-colors duration-fast ease-apple">
                  <p className="text-label tracking-label leading-label font-semibold text-text-tertiary uppercase">翻譯</p>
                  <p className="text-title tracking-title leading-title font-semibold text-text-primary break-words whitespace-pre-line">
                    {formatMultiline(current.translation)}
                  </p>
                  <p className="text-body tracking-body leading-body text-text-secondary break-all">
                    <span className="text-text-primary font-medium">{current.word}</span>
                    {current.ipa && <span className="text-text-tertiary font-mono"> {current.ipa}</span>}
                  </p>
                  {current.sourceSentence && (
                    <div className="mt-2 border-t border-border pt-3 space-y-2">
                      <p className="text-caption tracking-caption leading-caption text-text-tertiary italic">{current.sourceSentence}</p>
                      <p className="text-caption tracking-caption leading-caption text-text-tertiary">
                        來自《{current.sourceEpisodeId}》
                      </p>
                      {current.sourceEpisodeId && (
                        <div onClick={e => e.stopPropagation()} className="inline-block">
                          <ReplayAudioButton
                            episodeSlug={current.sourceEpisodeId}
                            timestamp={current.sourceTimestamp}
                          />
                        </div>
                      )}
                    </div>
                  )}
                  {current.mnemonic && (
                    <div onClick={e => e.stopPropagation()} className="pt-2">
                      <MnemonicHint text={current.mnemonic} />
                    </div>
                  )}
                </div>
              </motion.div>
            </motion.div>

            <div className="grid grid-cols-4 gap-2">
              <motion.button
                onClick={() => answer(1)}
                whileTap={{ scale: 0.94 }}
                transition={press}
                className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-bg-secondary text-text-secondary shadow-sm hover:bg-border transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Frown size={18} />
                <span className="text-caption tracking-caption leading-caption font-medium">忘記了</span>
              </motion.button>
              <motion.button
                onClick={() => answer(3)}
                whileTap={{ scale: 0.94 }}
                transition={press}
                className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-warning/10 text-warning shadow-sm hover:bg-warning/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Meh size={18} />
                <span className="text-caption tracking-caption leading-caption font-medium">有點難</span>
              </motion.button>
              <motion.button
                onClick={() => answer(4)}
                whileTap={{ scale: 0.94 }}
                transition={press}
                className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-success/10 text-success shadow-sm hover:bg-success/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Smile size={18} />
                <span className="text-caption tracking-caption leading-caption font-medium">記得</span>
              </motion.button>
              <motion.button
                onClick={() => answer(5)}
                whileTap={{ scale: 0.94 }}
                transition={press}
                className="inline-flex flex-col items-center justify-center gap-1.5 min-h-[60px] py-3 rounded-lg bg-accent/10 text-accent shadow-sm hover:bg-accent/20 transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <SmilePlus size={18} />
                <span className="text-caption tracking-caption leading-caption font-medium">很簡單</span>
              </motion.button>
            </div>

            <div className="flex items-center justify-center gap-4 text-caption tracking-caption leading-caption text-text-tertiary pt-2">
              <span className="text-warning">忘記 {forgotten}</span>
              <span>·</span>
              <span className="text-success">記得 {remembered}</span>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}
