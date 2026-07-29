// 管理後台側邊導覽項目。新增後台頁面 = 這裡加一筆 + App.tsx 加一行 <Route>。
// 順序即側邊欄顯示順序。

import { BarChart3, RadioTower, type LucideIcon } from 'lucide-react'

export const ADMIN_NAV = [
  { to: 'episodes', label: '單集數據', Icon: BarChart3 },
  { to: 'channels', label: '頻道管理', Icon: RadioTower },
] as const satisfies ReadonlyArray<{ to: string; label: string; Icon: LucideIcon }>
