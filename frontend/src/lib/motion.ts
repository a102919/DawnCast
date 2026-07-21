import { useReducedMotion } from 'framer-motion'
import type { Transition } from 'framer-motion'

// 手勢/拖曳/翻面用彈簧；滑鼠 hover 顏色變化留用 CSS transition-colors ease-apple。
export const springs = {
  // 一般 UI 結算：選單、tab 指示器、toggle 滑塊
  snappy: { type: 'spring', bounce: 0, duration: 0.3 } as const satisfies Transition,
  // sheet 無手勢情況下的開合、卡片翻面
  gentle: { type: 'spring', bounce: 0, duration: 0.4 } as const satisfies Transition,
  // 唯一用在「有拖曳動量」的釋放結算（drag-to-dismiss 甩出去的手感）
  bouncy: { type: 'spring', bounce: 0.2, duration: 0.4 } as const satisfies Transition,
  // framer-motion whileTap 按壓回饋
  press: { type: 'spring', bounce: 0, duration: 0.15 } as const satisfies Transition,
} as const

export type SpringName = keyof typeof springs

export const reducedMotionSprings: Record<SpringName, Transition> = {
  snappy: { type: 'tween', duration: 0.15, ease: 'easeOut' },
  gentle: { type: 'tween', duration: 0.15, ease: 'easeOut' },
  bouncy: { type: 'tween', duration: 0.15, ease: 'easeOut' },
  press: { type: 'tween', duration: 0.1, ease: 'easeOut' },
}

export function useSprings(): Record<SpringName, Transition> {
  const reduce = useReducedMotion()
  return reduce ? reducedMotionSprings : springs
}
