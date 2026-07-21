import { motion } from 'framer-motion'
import { useSprings } from '../../lib/motion'

interface ToggleProps {
  readonly checked: boolean
  readonly onChange: (checked: boolean) => void
  readonly label?: string
  readonly disabled?: boolean
}

export function Toggle({ checked, onChange, label, disabled = false }: ToggleProps) {
  const { snappy } = useSprings()

  return (
    <label className={`inline-flex items-center gap-3 cursor-pointer ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}>
      <div
        role="switch"
        aria-checked={checked}
        aria-label={label}
        tabIndex={disabled ? -1 : 0}
        onClick={() => !disabled && onChange(!checked)}
        onKeyDown={e => e.key === ' ' && !disabled && onChange(!checked)}
        className={`relative w-10 h-6 rounded-full transition-colors duration-base ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 ${
          checked ? 'bg-accent' : 'bg-border'
        }`}
      >
        <motion.span
          className="absolute top-0.5 left-0.5 w-5 h-5 bg-bg-elevated rounded-full shadow-sm"
          animate={{ x: checked ? 16 : 0 }}
          transition={snappy}
        />
      </div>
      {label && <span className="text-sm text-text-primary">{label}</span>}
    </label>
  )
}
