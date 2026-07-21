import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import { Button } from './Button'

interface EmptyStateAction {
  readonly label: string
  readonly to: string
  readonly variant?: 'primary' | 'link'
}

interface EmptyStateProps {
  readonly icon: LucideIcon
  readonly title: string
  readonly description?: ReactNode
  readonly action?: EmptyStateAction
  readonly size?: 'compact' | 'full'
}

export function EmptyState({ icon: Icon, title, description, action, size = 'full' }: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center text-center gap-3 ${size === 'full' ? 'py-20' : 'py-10'}`}>
      <div className="w-12 h-12 rounded-full bg-bg-secondary flex items-center justify-center text-text-tertiary">
        <Icon size={22} />
      </div>
      <div className="text-text-secondary text-sm">
        <p className="font-medium text-text-primary mb-1">{title}</p>
        {description && <p>{description}</p>}
      </div>
      {action && (action.variant === 'link' ? (
        <Link to={action.to} className="text-xs text-accent hover:underline">
          {action.label} →
        </Link>
      ) : (
        <Link to={action.to} className="mt-1">
          <Button variant="primary" size="sm">{action.label}</Button>
        </Link>
      ))}
    </div>
  )
}
