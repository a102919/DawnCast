import { type ReactNode } from 'react'

interface CardProps {
  readonly children: ReactNode
  readonly className?: string
  readonly padding?: 'none' | 'sm' | 'md' | 'lg'
}

const paddingClass = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
} as const

export function Card({ children, className = '', padding = 'md' }: CardProps) {
  return (
    <div
      className={`bg-bg-primary rounded-lg border border-border transition-[box-shadow,border-color] duration-fast ease-apple shadow-sm ${paddingClass[padding]} ${className}`}
    >
      {children}
    </div>
  )
}
