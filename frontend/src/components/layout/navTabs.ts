import { Home, BookOpen, Heart, RadioTower } from 'lucide-react'

// 「每日」從底部導覽移出讓位給「頻道」：訂單行事曆/歷史改從首頁的入口卡進入
// （見 HomeRoute.tsx），/daily 路由本身不受影響。
export const NAV_TABS = [
  { path: '/', label: '首頁', Icon: Home },
  { path: '/vocab', label: '單字本', Icon: BookOpen },
  { path: '/favorites', label: '收藏', Icon: Heart },
  { path: '/channels', label: '頻道', Icon: RadioTower },
] as const

/** 沉浸式頁面：不套使用者端 chrome（TopBar／BottomNav／MiniPlayer）。
 *  /login 是既有案例；/admin 自帶側邊導覽，兩套導覽疊在一起沒有意義。 */
export function isImmersivePath(pathname: string): boolean {
  return pathname === '/login' || pathname.startsWith('/admin')
}
