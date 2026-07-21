import { AlertCircle, RotateCcw } from 'lucide-react'

interface ErrorBannerProps {
  readonly message: string
  readonly onRetry?: () => void
  readonly variant?: 'inline' | 'block'
  readonly retryLabel?: string
  readonly className?: string
}

export function ErrorBanner({ message, onRetry, variant = 'block', retryLabel = '重試', className }: ErrorBannerProps) {
  if (variant === 'inline') {
    return (
      <div className={`flex items-center gap-2 px-3 py-2 rounded-lg bg-danger/10 border border-danger/20 text-danger text-xs ${className ?? ''}`}>
        <AlertCircle size={14} className="shrink-0" />
        <span className="flex-1">{message}</span>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="flex items-center gap-1 font-medium rounded hover:opacity-80 transition-opacity duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger/60"
          >
            <RotateCcw size={12} />
            {retryLabel}
          </button>
        )}
      </div>
    )
  }

  return (
    <div className={`flex flex-col items-center justify-center gap-3 ${className ?? 'py-20'}`}>
      <AlertCircle size={32} className="text-danger" />
      <p className="text-danger text-sm">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="flex items-center gap-1.5 px-4 py-2 text-sm text-text-secondary bg-bg-secondary hover:bg-border rounded-md transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <RotateCcw size={14} />
          {retryLabel}
        </button>
      )}
    </div>
  )
}
