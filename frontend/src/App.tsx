import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Toaster } from 'sonner'
import { useEffect } from 'react'
import { AuthProvider, ActivityProvider, PlayerProvider, VocabProvider, SettingsProvider, FavoritesProvider, DailyOrderProvider, useAuth, usePlayer } from './state'
import { TopBar, BottomNav } from './components/layout'
import { GlobalAudioHost, MiniPlayer } from './components/player'
import { HomeRoute, PlayerRoute, VocabRoute, FavoritesRoute, SettingsRoute, ProgressRoute, FlashcardRoute, DailyRoute, LoginRoute, AdminRoute } from './routes'
import { useSprings } from './lib/motion'
import { UpdatePrompt } from './components/UpdatePrompt'
import { PushPrompt } from './components/PushPrompt'

// 進入 PlayerRoute 用「往上推入」的位移感（呼應 Home 精選卡→播放頁的層級深入），
// 其餘頁面互轉維持單純淡入淡出，避免非相關頁面切換也有位移感造成雜訊。
const pageVariants = {
  initial: (isPlayer: boolean) => ({ opacity: 0, y: isPlayer ? 16 : 0 }),
  animate: { opacity: 1, y: 0 },
  exit: (isPlayer: boolean) => ({ opacity: 0, y: isPlayer ? -16 : 0 }),
}

// 動畫 wrapper：依 location.pathname 切換並 scroll 重置。
// auth gate 在外層 AppShell；這裡只管動畫。
function AnimatedRoutes() {
  const location = useLocation()
  const { gentle } = useSprings()
  const isPlayer = location.pathname.startsWith('/player')

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={location.pathname}
        custom={isPlayer}
        variants={pageVariants}
        initial="initial"
        animate="animate"
        exit="exit"
        transition={gentle}
      >
        <Routes location={location}>
          <Route path="/" element={<HomeRoute />} />
          <Route path="/player" element={<PlayerRoute />} />
          <Route path="/player/:id" element={<PlayerRoute />} />
          <Route path="/vocab" element={<VocabRoute />} />
          <Route path="/favorites" element={<FavoritesRoute />} />
          <Route path="/daily" element={<DailyRoute />} />
          <Route path="/settings" element={<SettingsRoute />} />
          <Route path="/progress" element={<ProgressRoute />} />
          <Route path="/flashcards" element={<FlashcardRoute />} />
          <Route path="/login" element={<LoginRoute />} />
          <Route path="/admin" element={<AdminRoute />} />
        </Routes>
      </motion.div>
    </AnimatePresence>
  )
}

function AuthenticatedContent() {
  const { pathname } = useLocation()
  const { currentEpisode } = usePlayer()
  const isImmersive = pathname === '/login'
  const isPlayer = pathname.startsWith('/player')
  const hasMiniPlayer = currentEpisode !== null && !isPlayer && !isImmersive

  return (
    <div className={isImmersive ? 'bg-bg-primary text-text-primary font-sans' : 'min-h-screen bg-bg-primary text-text-primary font-sans'}>
      <TopBar />
      <main className={
        isImmersive
          ? ''
          : hasMiniPlayer
            ? 'pb-[calc(7.125rem+env(safe-area-inset-bottom))] lg:pb-0'
            : 'pb-[calc(3.5rem+env(safe-area-inset-bottom))] lg:pb-0'
      }>
        <AnimatedRoutes />
      </main>
      <GlobalAudioHost />
      <MiniPlayer />
      <BottomNav />
    </div>
  )
}

// 登入後才掛載：包住所有 data provider，避免未登入時 mount 觸發 401。
function AuthenticatedShell() {
  return (
    <SettingsProvider>
      <ActivityProvider>
        <PlayerProvider>
          <VocabProvider>
            <FavoritesProvider>
              <DailyOrderProvider>
                <AuthenticatedContent />
              </DailyOrderProvider>
            </FavoritesProvider>
          </VocabProvider>
        </PlayerProvider>
      </ActivityProvider>
    </SettingsProvider>
  )
}

// 未登入樹：只 render LoginRoute，其他路徑一律導去 /login。
function UnauthenticatedShell() {
  return (
    <Routes>
      <Route path="/login" element={<LoginRoute />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  )
}

// 依 session 狀態切換兩棵樹。未登入樹沒有任何 data provider，
// 5 個會在 mount 打 API 的 provider 完全不會被建立，console 不會再有 401。
function AppShell() {
  const { session, isLoading } = useAuth()
  // session 初判前不 render 任何東西，避免閃頁或掛到一半切換。
  if (isLoading) return null
  if (!session) return <UnauthenticatedShell />
  return <AuthenticatedShell />
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppShell />
      </AuthProvider>
      <Toaster
        position="top-right"
        className="dc-toaster"
        closeButton
        offset={{ top: 'calc(4rem + env(safe-area-inset-top, 0px))', right: '1rem' }}
        mobileOffset={{ top: 'calc(4rem + env(safe-area-inset-top, 0px))', left: '1rem', right: '1rem' }}
        toastOptions={{
          unstyled: true,
          className: 'dc-toast',
          descriptionClassName: 'dc-toast-description',
          closeButtonAriaLabel: '關閉通知',
        }}
      />
      <UpdatePrompt />
      <PushPrompt />
    </BrowserRouter>
  )
}
