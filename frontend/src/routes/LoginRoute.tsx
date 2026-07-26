import { useState } from 'react'
import { Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '../components/primitives/Button'
import { ErrorBanner } from '../components/primitives/ErrorBanner'
import { useAuth } from '../state'
import { useSprings } from '../lib/motion'

type Status = 'idle' | 'redirecting' | 'error'

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72A5.4 5.4 0 0 1 3.68 9c0-.6.1-1.18.29-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.05l3.01-2.33z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.51.46 3.44 1.35l2.59-2.59C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"
      />
    </svg>
  )
}

export function LoginRoute() {
  const { signInWithGoogle } = useAuth()
  const [status, setStatus] = useState<Status>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const { gentle } = useSprings()

  const handleSignIn = async () => {
    setStatus('redirecting')
    setErrorMsg(null)
    try {
      await signInWithGoogle()
    } catch (err) {
      setStatus('error')
      setErrorMsg(err instanceof Error ? err.message : 'Google 登入失敗，請稍後再試')
    }
  }

  return (
    <div className="relative flex h-dvh flex-col overflow-hidden bg-bg-canvas">
      {/* 晨曦光暈：呼應 DawnCast 品牌色（朝日暖金 #f59e0b），營造破曉氛圍深度 */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-32 left-1/2 h-[28rem] w-[28rem] -translate-x-1/2 rounded-full bg-gradient-to-br from-[#f59e0b]/20 via-[#f97316]/15 to-transparent blur-3xl dark:from-[#f59e0b]/30 dark:via-[#f97316]/20"
      />

      <div className="relative flex items-center px-4 pt-[max(1rem,env(safe-area-inset-top))]">
        <Link
          to="/"
          aria-label="返回首頁"
          className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-full text-text-secondary transition-[background-color,color] duration-fast ease-apple hover:bg-bg-secondary hover:text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          <ArrowLeft size={20} />
        </Link>
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col items-center justify-center gap-4 px-6 text-center">
        <motion.img
          src="/favicon.svg"
          alt="DawnCast"
          initial={{ opacity: 0, scale: 0.9, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          transition={gentle}
          className="h-20 w-20 drop-shadow-[0_8px_24px_rgba(245,158,11,0.3)]"
        />
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ ...gentle, delay: 0.05 }}
          className="space-y-2"
        >
          <h1 className="text-display tracking-display leading-display font-bold text-text-primary">DawnCast</h1>
          <p className="text-sm leading-relaxed text-text-secondary">使用 Google 帳號登入，免設定密碼。</p>
        </motion.div>
      </div>

      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...gentle, delay: 0.1 }}
        className="relative mx-auto w-full max-w-sm space-y-3 px-6 pb-[max(2rem,env(safe-area-inset-bottom))] pt-4"
      >
        {status === 'error' && errorMsg && <ErrorBanner variant="inline" message={errorMsg} className="text-xs" />}

        <Button
          variant="google"
          size="lg"
          className="w-full justify-center rounded-full! transition-shadow"
          disabled={status === 'redirecting'}
          onClick={() => void handleSignIn()}
        >
          {status === 'redirecting' ? (
            <>
              <Loader2 size={16} className="animate-spin" />
              正在導向 Google
            </>
          ) : (
            <>
              <GoogleIcon />
              使用 Google 帳號登入
            </>
          )}
        </Button>
      </motion.div>
    </div>
  )
}
