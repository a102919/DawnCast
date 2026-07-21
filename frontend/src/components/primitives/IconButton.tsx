import { type ReactNode, type ButtonHTMLAttributes } from 'react'

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  readonly children: ReactNode
  readonly label: string
  readonly size?: 'sm' | 'md' | 'lg'
}

// sm 用於桌面密集工具列（非主要觸控動作），md/lg 維持 44px+ 觸控熱區
const sizeClass = {
  sm: 'min-w-[36px] min-h-[36px] p-2',
  md: 'min-w-[44px] min-h-[44px] p-[12px]',
  lg: 'min-w-[48px] min-h-[48px] p-3',
} as const

export function IconButton({ children, label, size = 'md', className = '', ...props }: IconButtonProps) {
  return (
    <button
      aria-label={label}
      className={`inline-flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-bg-secondary transition-[background-color,color,transform] duration-fast ease-apple cursor-pointer active:scale-[0.94] disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-1 ${sizeClass[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  )
}
