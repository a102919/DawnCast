import { useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertCircle, ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '../components/primitives/Button'
import { useAuth } from '../state'

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
    <div className="max-w-sm mx-auto px-4 pt-12 pb-8 space-y-6">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-xs text-text-tertiary hover:text-accent transition-colors duration-fast"
      >
        <ArrowLeft size={13} />
        返回首頁
      </Link>

      <div className="text-center space-y-2">
        <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-accent/10 text-accent">
          <GoogleIcon />
        </div>
        <h1 className="text-xl font-bold text-text-primary">登入 DawnCast</h1>
        <p className="text-sm text-text-secondary leading-relaxed">
          使用 Google 帳號登入，免設定密碼。
        </p>
      </div>

      {status === 'error' && errorMsg && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-danger/10 border border-danger/20 text-danger text-xs">
          <AlertCircle size={14} className="shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      <Button
        variant="primary"
        size="lg"
        className="w-full justify-center"
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
    </div>
  )
}
