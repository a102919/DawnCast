export function formatDateZhTW(isoDate: string): string {
  // ponytail: DB 偶發拿到空字串（舊資料 migration 未跑齊、手動匯入、seed 無值）時，
  // new Date('') 是 Invalid Date，Intl 直接 format 會 RangeError 把整個 HomeRoute 炸白。
  // fallback 回原文（空字串也 OK，UI 留空比整頁 crash 好）。
  // 正常路徑：migration 0018 把 episodes.published_at 補上 current_date default + backfill，
  // 不會再走到 fallback。
  const d = new Date(isoDate)
  if (Number.isNaN(d.getTime())) return isoDate
  return new Intl.DateTimeFormat('zh-TW', { month: 'long', day: 'numeric' }).format(d)
}

/** LLM 產出的多行文字（釋義、記憶提示等）以字面 `\n` 存在資料庫，這裡還原成真正換行。 */
export function formatMultiline(text: string): string {
  return text.replaceAll('\\n', '\n')
}

export { formatTime as formatTimestamp } from './time'

export function formatPos(pos: readonly string[]): string {
  const map: Record<string, string> = {
    n: '名詞', v: '動詞', a: '形容詞', r: '副詞',
    vd: '過去式', vg: '現在分詞', vi: '第三人稱單數',
    vn: '動名詞', zz: '其他',
  } as const
  return pos.map(p => map[p] ?? p).join('、')
}

export function formatExchange(exchange: string): string {
  const parts = exchange.split('/')
  const labels: Record<string, string> = {
    p: '過去式', d: '過去分詞', i: '現在分詞',
    '3': '第三人稱', r: '比較級', t: '最高級',
    s: '複數', 0: '原形',
  } as const
  return parts
    .map(part => {
      const colon = part.indexOf(':')
      if (colon === -1) return null
      const key = part.slice(0, colon)
      const val = part.slice(colon + 1)
      return labels[key] ? `${labels[key]}：${val}` : null
    })
    .filter((x): x is string => x !== null)
    .join('　')
}
