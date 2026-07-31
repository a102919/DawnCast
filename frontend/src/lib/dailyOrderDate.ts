// 日期純函式。放在 lib/ 是因為這層只有 pure function、沒有 React 依賴，
// 與既有 lib/format.ts、lib/time.ts 同層級。

/** 取本地時區的 YYYY-MM-DD。en-CA locale 剛好就是 ISO 順序。 */
export function toIsoDate(d: Date): string {
  return d.toLocaleDateString('en-CA')
}
