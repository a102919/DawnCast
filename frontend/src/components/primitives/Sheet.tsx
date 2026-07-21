import { useEffect, useState, type ReactNode } from 'react'
import { AnimatePresence, motion, type PanInfo } from 'framer-motion'
import { springs } from '../../lib/motion'

interface SheetProps {
  readonly isOpen: boolean
  readonly onClose: () => void
  readonly variant: 'bottom' | 'side'
  readonly children: ReactNode
  readonly ariaLabelledBy: string
  readonly maxHeight?: string
  readonly widthClassName?: string
  readonly dismissible?: boolean
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
}: SheetProps) {
  const [exitVelocity, setExitVelocity] = useState(0)
  const axis = variant === 'bottom' ? 'y' : 'x'

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
    const shouldClose = velocity > 500 || (offset > 100 && velocity >= 0)
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
            className={
              variant === 'bottom'
                ? 'fixed bottom-nav-sheet left-0 right-0 z-50 material-regular rounded-t-xl border-t border-border shadow-lg flex flex-col'
                : `fixed top-0 right-0 h-full z-50 material-regular shadow-lg flex flex-col ${widthClassName}`
            }
            style={variant === 'bottom' ? { maxHeight } : undefined}
            initial={variant === 'bottom' ? { y: '100%' } : { x: '100%' }}
            animate={
              variant === 'bottom'
                ? { y: 0, transition: springs.gentle }
                : { x: 0, transition: springs.gentle }
            }
            exit={
              variant === 'bottom'
                ? { y: '100%', transition: { ...springs.bouncy, velocity: exitVelocity } }
                : { x: '100%', transition: { ...springs.bouncy, velocity: exitVelocity } }
            }
            drag={dismissible ? axis : false}
            dragConstraints={axis === 'y' ? { top: 0 } : { left: 0 }}
            dragElastic={axis === 'y' ? { top: 0, bottom: 0.5 } : { left: 0, right: 0.5 }}
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
