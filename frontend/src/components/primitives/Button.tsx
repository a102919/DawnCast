import { type ReactNode, type ButtonHTMLAttributes } from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly variant?: Variant
  readonly size?: Size
  readonly children: ReactNode
}

const variantClass: Record<Variant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover',
  secondary: 'bg-bg-secondary border border-border text-text-primary hover:bg-border',
  ghost: 'text-text-primary hover:bg-bg-secondary',
  danger: 'text-danger hover:bg-danger/10',
} as const

const sizeClass: Record<Size, string> = {
  sm: 'px-3 py-1.5 text-xs rounded-sm min-h-[44px]',
  md: 'px-4 py-2 text-sm rounded-md min-h-[44px]',
  lg: 'px-5 py-2.5 text-base rounded-md',
} as const

export function Button({ variant = 'secondary', size = 'md', children, className = '', ...props }: ButtonProps) {
  return (
    <button
      className={`inline-flex items-center gap-1.5 font-medium transition-colors duration-fast ease-apple cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 ${variantClass[variant]} ${sizeClass[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}
