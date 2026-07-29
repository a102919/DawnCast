// 管理後台側邊導覽：桌面固定左側欄，行動版收進既有 Sheet（side variant，
// 不另外刻抽屜）。Active 指示沿用 TopBar 同一套 layoutId + snappy spring
// 模式——長得一樣的東西必須表現一樣（apple-design：Familiarity）。

import { useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Menu, X } from 'lucide-react'
import { ADMIN_NAV } from './nav'
import { useSprings } from '../../lib/motion'
import { Sheet } from '../../components/primitives'

interface AdminNavLinksProps {
  readonly onNavigate?: () => void
}

function AdminNavLinks({ onNavigate }: AdminNavLinksProps) {
  const { pathname } = useLocation()
  const { snappy } = useSprings()

  return (
    <nav aria-label="管理後台導覽" className="flex flex-col gap-1 p-3">
      {ADMIN_NAV.map(({ to, label, Icon }) => {
        const active = pathname === `/admin/${to}`
        return (
          <Link
            key={to}
            to={`/admin/${to}`}
            onClick={onNavigate}
            className={`relative flex items-center gap-2.5 px-3 h-10 rounded-md text-sm font-medium transition-colors duration-fast ease-apple ${
              active ? 'text-accent' : 'text-text-secondary hover:text-text-primary hover:bg-bg-secondary'
            }`}
          >
            {active && (
              <motion.div
                layoutId="admin-nav-indicator"
                transition={snappy}
                className="absolute inset-0 rounded-md bg-bg-secondary -z-10"
              />
            )}
            <Icon size={16} strokeWidth={active ? 2.5 : 2} />
            {label}
          </Link>
        )
      })}
    </nav>
  )
}

export function AdminSidebar() {
  const [mobileOpen, setMobileOpen] = useState(false)

  return (
    <>
      {/* 桌面固定側欄 */}
      <aside className="hidden lg:flex flex-col w-56 shrink-0 min-h-screen material-thin border-r border-border">
        <div className="h-14 flex items-center px-4 border-b border-border">
          <Link
            to="/"
            className="flex items-center gap-2 text-sm font-bold text-text-primary tracking-tight hover:opacity-90 transition-opacity"
          >
            <img src="/favicon.svg" alt="" className="w-6 h-6 shrink-0 object-contain" />
            管理後台
          </Link>
        </div>
        <div className="flex-1">
          <AdminNavLinks />
        </div>
        <div className="p-3 border-t border-border">
          <Link
            to="/"
            className="flex items-center gap-1.5 px-3 h-8 rounded-md text-xs text-text-secondary transition-colors duration-fast ease-apple hover:text-text-primary hover:bg-bg-secondary"
          >
            <ArrowLeft size={14} />
            返回 DawnCast
          </Link>
        </div>
      </aside>

      {/* 行動版：sticky 頂列 + 漢堡鈕開 Sheet */}
      <header className="lg:hidden sticky top-0 z-40 material-thin border-b border-border">
        <div className="h-[env(safe-area-inset-top,0px)]" />
        <div className="h-14 flex items-center justify-between px-4">
          <span className="text-sm font-semibold text-text-primary">管理後台</span>
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-label="開啟導覽選單"
            className="p-2 -m-2 rounded-md text-text-secondary transition-colors duration-fast ease-apple hover:text-text-primary"
          >
            <Menu size={20} />
          </button>
        </div>
      </header>

      <Sheet
        isOpen={mobileOpen}
        onClose={() => setMobileOpen(false)}
        variant="side"
        ariaLabelledBy="admin-mobile-nav-title"
        widthClassName="w-64 max-w-[80vw]"
      >
        <div className="h-14 flex items-center justify-between px-4 border-b border-border shrink-0">
          <span id="admin-mobile-nav-title" className="text-sm font-semibold text-text-primary">
            管理後台
          </span>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            aria-label="關閉導覽選單"
            className="p-2 -m-2 rounded-md text-text-secondary transition-colors duration-fast ease-apple hover:text-text-primary"
          >
            <X size={20} />
          </button>
        </div>
        <AdminNavLinks onNavigate={() => setMobileOpen(false)} />
        <div className="mt-auto p-3 border-t border-border shrink-0">
          <Link
            to="/"
            onClick={() => setMobileOpen(false)}
            className="flex items-center gap-1.5 px-3 h-8 rounded-md text-xs text-text-secondary transition-colors duration-fast ease-apple hover:text-text-primary hover:bg-bg-secondary"
          >
            <ArrowLeft size={14} />
            返回 DawnCast
          </Link>
        </div>
      </Sheet>
    </>
  )
}
