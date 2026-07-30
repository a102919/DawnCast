import { useRef, type ReactNode } from 'react'
import { motion, useMotionValue, useTransform, type PanInfo } from 'framer-motion'
import { Check, X } from 'lucide-react'
import { springs, useSprings } from '../../lib/motion'
import { decideSwipe, type SwipeDirection, type SwipeExit } from '../../lib/swipe'

const swipeVariants = {
  exit: (c: SwipeExit | null) => {
    if (!c || c.reduce) return { opacity: 0, transition: { duration: 0.15, ease: 'easeOut' as const } }
    return {
      x: (c.dir === 'right' ? 1 : -1) * (window.innerWidth + 200),
      // 唯一帶動量的結算：把手指釋放速度交棒給 spring，drag → animate 無縫
      transition: { ...springs.bouncy, velocity: c.velocity },
    }
  },
}

interface SwipeCardProps {
  readonly onSwipe: (dir: SwipeDirection, velocity: number) => void
  readonly leftLabel: string
  readonly rightLabel: string
  readonly children: ReactNode
}

/** Tinder 式滑卡外殼：drag 1:1 跟手、rotate/徽章隨拖曳即時回饋、未達標彈回。
 *  飛出動畫由 AnimatePresence exit 執行（父層在 onSwipe 後換 key），樂觀更新不等動畫。 */
export function SwipeCard({ onSwipe, leftLabel, rightLabel, children }: SwipeCardProps) {
  const { gentle, reduce } = useSprings()
  const x = useMotionValue(0)
  // reduced-motion：1:1 拖曳保留（direct manipulation 非前庭刺激），rotate 歸零
  const rotate = useTransform(x, [-240, 240], reduce ? [0, 0] : [-12, 12])
  const rightOpacity = useTransform(x, [24, 100], [0, 1])
  const leftOpacity = useTransform(x, [-100, -24], [1, 0])
  const draggedRef = useRef(false)

  const handleDragEnd = (_: unknown, info: PanInfo) => {
    const dir = decideSwipe(info.offset.x, info.velocity.x)
    if (dir) onSwipe(dir, info.velocity.x)
    // click 事件在 dragend 同一個 task 內接著派發，setTimeout 讓攔截旗標活過它
    setTimeout(() => { draggedRef.current = false }, 0)
  }

  return (
    <motion.div
      drag="x"
      dragSnapToOrigin
      style={{ x, rotate }}
      variants={swipeVariants}
      initial={reduce ? { opacity: 0 } : { scale: 0.96, y: 8, opacity: 0.9 }}
      animate={reduce ? { opacity: 1 } : { scale: 1, y: 0, opacity: 1, transition: gentle }}
      exit="exit"
      onDragStart={() => { draggedRef.current = true }}
      onDragEnd={handleDragEnd}
      onClickCapture={e => {
        // 拖曳結束後瀏覽器仍會補發 click，擋掉避免誤觸翻面
        if (draggedRef.current) {
          e.preventDefault()
          e.stopPropagation()
        }
      }}
      className="relative h-full cursor-grab active:cursor-grabbing"
    >
      <motion.div
        aria-hidden
        style={{ opacity: rightOpacity }}
        className="absolute top-3 right-3 z-10 inline-flex items-center gap-1 px-2.5 py-1 rounded-pill bg-success text-white text-caption tracking-caption leading-caption font-semibold shadow-sm pointer-events-none"
      >
        <Check size={12} strokeWidth={3} />
        {rightLabel}
      </motion.div>
      <motion.div
        aria-hidden
        style={{ opacity: leftOpacity }}
        className="absolute top-3 left-3 z-10 inline-flex items-center gap-1 px-2.5 py-1 rounded-pill bg-warning text-white text-caption tracking-caption leading-caption font-semibold shadow-sm pointer-events-none"
      >
        <X size={12} strokeWidth={3} />
        {leftLabel}
      </motion.div>
      {children}
    </motion.div>
  )
}
