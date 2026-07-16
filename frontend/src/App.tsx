import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import { AnimatePresence, motion } from 'framer-motion'
import { Toaster } from 'sonner'
import { useEffect } from 'react'
import { AuthProvider, PlayerProvider, VocabProvider, SettingsProvider, ListenedProvider, FavoritesProvider, DailyOrderProvider } from './state'
import { TopBar, BottomNav } from './components/layout'
import { HomeRoute, PlayerRoute, VocabRoute, FavoritesRoute, SettingsRoute, ProgressRoute, FlashcardRoute, DailyRoute, LoginRoute } from './routes'

const pageVariants = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
}

const pageTransition = {
  duration: 0.15,
  ease: [0.2, 0.8, 0.2, 1] as const,
}

function AnimatedRoutes() {
  const location = useLocation()

  useEffect(() => {
    window.scrollTo(0, 0)
  }, [location.pathname])

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={location.pathname}
        variants={pageVariants}
        initial="initial"
        animate="animate"
        exit="exit"
        transition={pageTransition}
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
        </Routes>
      </motion.div>
    </AnimatePresence>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <SettingsProvider>
          <ListenedProvider>
            <PlayerProvider>
              <VocabProvider>
                <FavoritesProvider>
                  <DailyOrderProvider>
                    <div className="min-h-screen bg-bg-primary text-text-primary font-sans">
                      <TopBar />
                      <main className="pb-14 lg:pb-0">
                        <AnimatedRoutes />
                      </main>
                      <BottomNav />
                    </div>
                  </DailyOrderProvider>
                </FavoritesProvider>
              </VocabProvider>
            </PlayerProvider>
          </ListenedProvider>
        </SettingsProvider>
      </AuthProvider>
      <Toaster position="bottom-center" richColors />
    </BrowserRouter>
  )
}
