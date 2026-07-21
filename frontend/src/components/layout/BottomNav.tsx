import { Link, useLocation } from 'react-router-dom'
import { motion } from 'framer-motion'
import { NAV_TABS } from './navTabs'
import { useSprings } from '../../lib/motion'

export function BottomNav() {
  const { pathname } = useLocation()
  const { snappy } = useSprings()

  if (pathname.startsWith('/player')) return null

  return (
    <nav className="lg:hidden fixed bottom-0 inset-x-0 material-thin border-t border-border z-40">
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
              className={`relative flex-1 flex flex-col items-center justify-center gap-0.5 transition-colors duration-fast focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-inset ${
                active ? 'text-accent' : 'text-text-tertiary hover:text-text-secondary'
              }`}
            >
              {active && (
                <motion.div
                  layoutId="bottomnav-tab-indicator"
                  transition={snappy}
                  className="absolute top-0 inset-x-6 h-0.5 rounded-full bg-accent"
                />
              )}
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
