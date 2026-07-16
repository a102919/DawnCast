import { type ReactNode } from 'react'

interface CardProps {
  readonly children: ReactNode
  readonly className?: string
  readonly elevated?: boolean
}

export function Card({ children, className = '', elevated = false }: CardProps) {
  return (
    <div
      className={`bg-bg-primary rounded-lg border border-border ${elevated ? 'shadow-md' : 'shadow-sm'} ${className}`}
    >
      {children}
    </div>
  )
}
