import { Link, useLocation } from 'react-router-dom'
import { Settings, ChevronLeft } from 'lucide-react'
import { NAV_TABS } from './navTabs'

export function TopBar() {
  const { pathname } = useLocation()
  const isSettingsActive = pathname.startsWith('/settings')
  const isPlayerPage = pathname.startsWith('/player')

  return (
    <header className="h-14 flex items-center justify-between px-5 border-b border-border bg-bg-primary/80 backdrop-blur-md sticky top-0 z-40">
      {isPlayerPage ? (
        <>
          <Link
            to="/"
            className="lg:hidden inline-flex items-center gap-0.5 text-text-secondary hover:text-text-primary transition-colors duration-fast ease-apple focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-md -ml-1 px-1 h-8"
            aria-label="返回首頁"
          >
            <ChevronLeft size={20} />
            <span className="text-sm font-medium">返回</span>
          </Link>
          <Link to="/" className="hidden lg:block text-lg font-semibold text-text-primary tracking-tight">
            DawnCast
          </Link>
        </>
      ) : (
        <Link to="/" className="text-lg font-semibold text-text-primary tracking-tight">
          DawnCast
        </Link>
      )}

      <div className="flex items-center gap-1">
        {/* Desktop only — mobile uses BottomNav */}
        <nav className="hidden lg:flex items-center gap-1">
          {NAV_TABS.map(({ path, label, Icon }) => {
            const isPlayerPath = pathname.startsWith('/player')
            const active = path === '/' ? pathname === '/' || isPlayerPath : pathname.startsWith(path)
            return (
              <Link
                key={path}
                to={path}
                className={`inline-flex items-center gap-1.5 px-3 h-8 rounded-md text-sm font-medium transition-colors duration-fast ease-apple hover:bg-bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                  active ? 'text-accent' : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                <Icon size={16} strokeWidth={active ? 2.5 : 2} />
                {label}
              </Link>
            )
          })}
        </nav>
        <Link
          to="/settings"
          aria-label="設定"
          className={`inline-flex items-center justify-center w-8 h-8 rounded-md transition-colors duration-fast ease-apple hover:bg-bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
            isSettingsActive ? 'text-accent' : 'text-text-secondary hover:text-text-primary'
          }`}
        >
          <Settings size={16} strokeWidth={isSettingsActive ? 2.5 : 2} />
        </Link>
      </div>
    </header>
  )
}
