import { useEffect, useState, type ReactNode } from 'react'
import { AnimatePresence, motion, type PanInfo } from 'framer-motion'
import { springs } from '../../lib/motion'

type SheetVariant = 'bottom' | 'side' | 'top'

interface SheetProps {
  readonly isOpen: boolean
  readonly onClose: () => void
  readonly variant: SheetVariant
  readonly children: ReactNode
  readonly ariaLabelledBy: string
  readonly maxHeight?: string
  readonly widthClassName?: string
  readonly dismissible?: boolean
  /** bottom variant 預設停在 BottomNav 上方；在沒有 BottomNav 的頁面（如 /admin）
   *  設 false 讓 sheet 貼齊螢幕底部，否則會空出一條 nav 高度的縫。 */
  readonly aboveBottomNav?: boolean
}

// 3 個 variant 各自的 className / 進出場位移 / drag 邊界，原本用巢狀三元運算式表達，
// 這裡收斂成查表函式——值本身（spring 設定、位移量、drag 彈性）完全不變，只是換一種
// 表達分支的寫法。
function sheetClassName(variant: SheetVariant, aboveBottomNav: boolean, widthClassName: string): string {
  if (variant === 'side') return `fixed top-0 right-0 h-full z-50 material-regular shadow-lg flex flex-col ${widthClassName}`
  if (variant === 'top') {
    return 'fixed top-0 left-0 right-0 lg:left-auto lg:right-4 lg:w-[min(32rem,calc(100vw-2rem))] z-50 material-regular rounded-b-xl border-b border-border shadow-lg flex flex-col pt-[env(safe-area-inset-top,0px)]'
  }
  return `fixed ${aboveBottomNav ? 'bottom-nav-sheet' : 'bottom-0'} left-0 right-0 z-50 material-regular rounded-t-xl border-t border-border shadow-lg flex flex-col`
}

function sheetInitial(variant: SheetVariant): { readonly x: string } | { readonly y: string } {
  if (variant === 'side') return { x: '100%' }
  return variant === 'top' ? { y: '-100%' } : { y: '100%' }
}

function sheetExit(variant: SheetVariant, exitVelocity: number) {
  const transition = { ...springs.bouncy, velocity: exitVelocity }
  if (variant === 'side') return { x: '100%', transition }
  return variant === 'top' ? { y: '-100%', transition } : { y: '100%', transition }
}

function sheetDragConstraints(variant: SheetVariant): { readonly top?: number; readonly bottom?: number; readonly left?: number } {
  if (variant === 'side') return { left: 0 }
  return variant === 'top' ? { bottom: 0 } : { top: 0 }
}

function sheetDragElastic(variant: SheetVariant): { readonly top?: number; readonly bottom?: number; readonly left?: number; readonly right?: number } {
  if (variant === 'side') return { left: 0, right: 0.5 }
  return variant === 'top' ? { top: 0.5, bottom: 0 } : { top: 0, bottom: 0.5 }
}

export function Sheet({
  isOpen,
  onClose,
  variant,
  children,
  ariaLabelledBy,
  maxHeight = '90vh',
  widthClassName = 'w-96 max-w-full',
  dismissible = true,
  aboveBottomNav = true,
}: SheetProps) {
  const [exitVelocity, setExitVelocity] = useState(0)
  const axis = variant === 'side' ? 'x' : 'y'
  const isTop = variant === 'top'

  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  const handleDragEnd = (_: unknown, info: PanInfo) => {
    const offset = axis === 'y' ? info.offset.y : info.offset.x
    const velocity = axis === 'y' ? info.velocity.y : info.velocity.x
    const shouldClose = isTop
      ? velocity < -500 || (offset < -100 && velocity <= 0)
      : velocity > 500 || (offset > 100 && velocity >= 0)
    if (shouldClose) {
      setExitVelocity(velocity)
      onClose()
    }
  }

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            className={`fixed inset-0 z-40 ${variant === 'bottom' ? 'scrim' : 'scrim-light'}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={dismissible ? onClose : undefined}
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-labelledby={ariaLabelledBy}
            className={sheetClassName(variant, aboveBottomNav, widthClassName)}
            style={variant === 'side' ? undefined : { maxHeight }}
            initial={sheetInitial(variant)}
            animate={
              variant === 'bottom' || isTop
                ? { y: 0, transition: springs.gentle }
                : { x: 0, transition: springs.gentle }
            }
            exit={sheetExit(variant, exitVelocity)}
            drag={dismissible ? axis : false}
            dragConstraints={sheetDragConstraints(variant)}
            dragElastic={sheetDragElastic(variant)}
            onDragEnd={dismissible ? handleDragEnd : undefined}
          >
            {variant === 'bottom' && (
              <div className="flex justify-center pt-3 pb-1 shrink-0">
                <div className="w-8 h-1 rounded-full bg-border" />
              </div>
            )}
            {children}
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}
