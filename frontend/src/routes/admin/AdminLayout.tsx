// 管理後台外殼：側邊導覽 + <Outlet />。X-Admin-Token 後門已於 2026-07-29 砍掉，
// admin 唯一授權路徑是 Supabase JWT（Google 登入）email 白名單——若未通過，
// 後端每個 endpoint 會 401，由 request() 統一處理、無法在 layout 端預判。
// 因此這裡不再有任何 token 判斷／設定 UI，子頁面直接掛載。

import { Outlet } from 'react-router-dom'
import { AdminSidebar } from './AdminSidebar'

export function AdminLayout() {
  return (
    <div className="lg:flex lg:min-h-screen">
      <AdminSidebar />

      {/* 桌面資料作業頁刻意比全站 max-w-2xl 寬——這裡是側邊欄 + 表格式內容,
          不是手機閱讀頁。 */}
      <div className="flex-1 min-w-0 max-w-5xl mx-auto px-4 py-6 space-y-6">
        <Outlet />
      </div>
    </div>
  )
}
