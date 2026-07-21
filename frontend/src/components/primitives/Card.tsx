import { type ReactNode } from 'react'

interface CardProps {
  readonly children: ReactNode
  readonly className?: string
  readonly elevated?: boolean
  readonly interactive?: boolean
  readonly padding?: 'none' | 'sm' | 'md' | 'lg'
}

const paddingClass = {
  none: '',
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
} as const

export function Card({ children, className = '', elevated = false, interactive = false, padding = 'md' }: CardProps) {
  return (
    <div
      className={`bg-bg-primary rounded-lg border border-border transition-[box-shadow,border-color] duration-fast ease-apple ${
        elevated ? 'shadow-md' : 'shadow-sm'
      } ${interactive ? 'cursor-pointer hover:border-accent/40 hover:shadow-md active:scale-[0.99]' : ''} ${paddingClass[padding]} ${className}`}
    >
      {children}
    </div>
  )
}
