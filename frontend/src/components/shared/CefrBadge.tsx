import { CEFR_COLOR, type CefrLevel } from '../../lib'

interface CefrBadgeProps {
  readonly level: CefrLevel
}

/** CEFR 難度小 chip：首頁/清單多處重複用的同一顆徽章，這裡收斂成一處。 */
export function CefrBadge({ level }: CefrBadgeProps) {
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${CEFR_COLOR[level]}`}>
      {level}
    </span>
  )
}
