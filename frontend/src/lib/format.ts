export function formatTimestamp(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

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
