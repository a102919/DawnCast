import { type ReactNode } from 'react'

export function SectionLabel({ children, className = '' }: { readonly children: ReactNode; readonly className?: string }) {
  return (
    <h2 className={`text-label tracking-label leading-label font-semibold text-text-tertiary uppercase ${className}`}>
      {children}
    </h2>
  )
}
