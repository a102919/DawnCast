import { AnimatePresence, motion } from 'framer-motion'
import { useLocation, useNavigate } from 'react-router-dom'
import { Headphones, Play, Pause } from 'lucide-react'
import { usePlayer } from '../../state'
import { useSprings } from '../../lib/motion'
import { computeProgress } from '../../lib/playback'

/** 離開播放頁後浮在 BottomNav 上方的迷你播放列（手機版）；點擊回全螢幕播放頁。 */
export function MiniPlayer() {
  const { currentEpisode, isPlaying, currentTime, duration, play, pause } = usePlayer()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { gentle, press } = useSprings()
  const visible = currentEpisode !== null && !pathname.startsWith('/player') && pathname !== '/login'
  const progress = computeProgress(currentTime, duration)

  return (
    <AnimatePresence>
      {visible && currentEpisode && (
        <motion.div
          key={currentEpisode.id}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 12 }}
          transition={gentle}
          // z-45：語意上浮在 BottomNav（z-40）之上，不能靠幾何剛好不重疊撐住
          // （見 bottom-nav-sheet 註解的 safe-area 漏算事故）；小於 Sheet 內容的 z-50。
          className="lg:hidden fixed inset-x-0 bottom-nav-sheet z-[45] material-thick border-t border-border"
        >
          <div className="h-0.5 bg-border">
            <div className="h-full bg-accent" style={{ width: `${progress}%` }} />
          </div>
          <div
            role="button"
            tabIndex={0}
            aria-label={`回到播放頁：${currentEpisode.title}`}
            onClick={() => navigate(`/player/${currentEpisode.id}`)}
            onKeyDown={e => { if (e.key === 'Enter') navigate(`/player/${currentEpisode.id}`) }}
            className="w-full h-14 flex items-center gap-3 px-4 cursor-pointer"
          >
            <span className="w-9 h-9 rounded-md bg-bg-secondary flex items-center justify-center shrink-0">
              <Headphones size={16} className="text-text-tertiary" />
            </span>
            <span className="flex-1 min-w-0 text-sm text-left truncate">{currentEpisode.title}</span>
            <motion.button
              type="button"
              aria-label={isPlaying ? '暫停' : '播放'}
              whileTap={{ scale: 0.9 }}
              transition={press}
              onClick={e => { e.stopPropagation(); if (isPlaying) { pause() } else { play() } }}
              className="w-9 h-9 rounded-full bg-accent flex items-center justify-center shrink-0"
            >
              {isPlaying
                ? <Pause size={16} fill="white" color="white" />
                : <Play size={16} fill="white" color="white" className="translate-x-px" />}
            </motion.button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
