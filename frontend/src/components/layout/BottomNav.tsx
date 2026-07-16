import { Link, useLocation } from 'react-router-dom'
import { NAV_TABS } from './navTabs'

export function BottomNav() {
  const { pathname } = useLocation()

  if (pathname.startsWith('/player')) return null

  return (
    <nav className="lg:hidden fixed bottom-0 inset-x-0 bg-bg-primary/80 backdrop-blur-md border-t border-border z-40">
      <div className="h-14 flex items-stretch">
        {NAV_TABS.map(({ path, label, Icon }) => {
          const isPlayerPath = pathname.startsWith('/player')
          const active =
            path === '/'
              ? pathname === '/' || isPlayerPath
              : pathname.startsWith(path)
          return (
            <Link
              key={path}
              to={path}
              className={`flex-1 flex flex-col items-center justify-center gap-0.5 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-inset ${
                active ? 'text-accent' : 'text-text-tertiary hover:text-text-secondary'
              }`}
            >
              <Icon size={20} strokeWidth={active ? 2.5 : 2} />
              <span className="text-[10px] font-medium leading-none">{label}</span>
            </Link>
          )
        })}
      </div>
      <div className="h-[env(safe-area-inset-bottom,0px)]" />
    </nav>
  )
}
