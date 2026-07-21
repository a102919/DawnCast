import { type ReactNode } from 'react'

interface ChipProps {
  readonly children: ReactNode
  readonly active?: boolean
  readonly onClick?: () => void
  readonly className?: string
}

export function Chip({ children, active = false, onClick, className = '' }: ChipProps) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center px-3 py-2.5 min-h-[44px] rounded-full text-xs font-medium transition-[background-color,color,border-color,transform] duration-fast ease-apple cursor-pointer active:scale-[0.97] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 ${
        active
          ? 'bg-accent text-white'
          : 'bg-bg-secondary text-text-secondary border border-border hover:border-accent hover:text-accent'
      } ${className}`}
    >
      {children}
    </button>
  )
}
