import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Loader2, AlertCircle } from 'lucide-react'
import { usePlayer } from '../../state'

interface VideoPlayerProps {
  readonly videoUrl: string
}

export function VideoPlayer({ videoUrl }: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const { setVideoRef, playbackRate } = usePlayer()
  const [isLoading, setIsLoading] = useState(true)
  const [hasError, setHasError] = useState(false)

  useEffect(() => {
    setVideoRef(videoRef.current)
    return () => setVideoRef(null)
  }, [setVideoRef])

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.playbackRate = playbackRate
    }
  }, [playbackRate])

  const easeApple = [0.2, 0.8, 0.2, 1] as const

  return (
    <div className="relative w-full rounded-lg overflow-hidden bg-black" style={{ aspectRatio: '16/9', maxHeight: '45vh' }}>
      <video
        ref={videoRef}
        src={videoUrl}
        className="w-full h-full object-contain"
        preload="metadata"
        playsInline
        onWaiting={() => setIsLoading(true)}
        onCanPlay={() => { setIsLoading(false); setHasError(false) }}
        onCanPlayThrough={() => { setIsLoading(false); setHasError(false) }}
        onError={() => { setIsLoading(false); setHasError(true) }}
      />
      <AnimatePresence>
        {isLoading && !hasError && (
          <motion.div
            key="loading"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2, ease: easeApple }}
            className="absolute inset-0 flex items-center justify-center bg-black/60"
          >
            <Loader2 className="w-10 h-10 text-accent animate-spin" />
          </motion.div>
        )}
        {hasError && (
          <motion.div
            key="error"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2, ease: easeApple }}
            className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/70"
          >
            <AlertCircle className="w-10 h-10 text-danger" />
            <span className="text-text-secondary text-sm">無法播放，請稍後再試</span>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
