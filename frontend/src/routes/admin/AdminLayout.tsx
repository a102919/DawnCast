// 管理後台外殼：側邊導覽 + <Outlet />。整個 admin 唯一的權杖判斷點——
// 子頁面不再各自處理 hasToken，沒權杖時內容區顯示提示，側邊欄照常渲染
// （導覽架構本身要看得到，不是被藏起來）。

import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { getAdminToken } from '../../api'
import { AdminSidebar } from './AdminSidebar'
import { AdminTokenCard } from './AdminTokenCard'

export function AdminLayout() {
  const [token, setToken] = useState<string | null>(getAdminToken())
  // 權杖已設定時預設收合；側欄的狀態鈕可以再點開來改／清除。
  const [tokenCardOpen, setTokenCardOpen] = useState(false)

  return (
    <div className="lg:flex lg:min-h-screen">
      <AdminSidebar hasToken={!!token} onToggleTokenCard={() => setTokenCardOpen(v => !v)} />

      {/* 桌面資料作業頁刻意比全站 max-w-2xl 寬——這裡是側邊欄 + 表格式內容，
          不是手機閱讀頁。 */}
      <div className="flex-1 min-w-0 max-w-5xl mx-auto px-4 py-6 space-y-6">
        {token ? (
          <>
            {tokenCardOpen && <AdminTokenCard token={token} onTokenChange={setToken} />}
            <Outlet />
          </>
        ) : (
          <AdminTokenCard token={token} onTokenChange={setToken} />
        )}
      </div>
    </div>
  )
}
