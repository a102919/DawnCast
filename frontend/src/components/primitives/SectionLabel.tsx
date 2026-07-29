import { type ReactNode } from 'react'

type SectionLabelSize = 'label' | 'headline'

const SIZE_CLASS: Record<SectionLabelSize, string> = {
  label: 'text-label tracking-label leading-label font-semibold text-text-tertiary uppercase',
  headline: 'text-headline tracking-headline leading-headline font-semibold text-text-primary',
}

export function SectionLabel({
  children,
  size = 'label',
  className = '',
}: {
  readonly children: ReactNode
  /** 'headline'：需要比一般小節眼標更醒目的入口（如首頁「頻道」）才用，預設維持小節眼標樣式。 */
  readonly size?: SectionLabelSize
  readonly className?: string
}) {
  return <h2 className={`${SIZE_CLASS[size]} ${className}`}>{children}</h2>
}
