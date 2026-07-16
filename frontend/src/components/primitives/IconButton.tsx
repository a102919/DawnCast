import { type ReactNode, type ButtonHTMLAttributes } from 'react'

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly children: ReactNode
  readonly label: string
  readonly size?: 'sm' | 'md' | 'lg'
}

const sizeClass = {
  sm: 'min-w-[44px] min-h-[44px] p-[14px]',
  md: 'min-w-[44px] min-h-[44px] p-[12px]',
  lg: 'min-w-[44px] min-h-[44px] p-[10px]',
} as const

export function IconButton({ children, label, size = 'md', className = '', ...props }: IconButtonProps) {
  return (
    <button
      aria-label={label}
      className={`inline-flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-secondary transition-colors duration-fast ease-apple cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 ${sizeClass[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}
