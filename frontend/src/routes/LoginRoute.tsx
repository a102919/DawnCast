import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Mail, CheckCircle2, AlertCircle, ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '../components/primitives/Button'
import { useAuth } from '../state'

type Status = 'idle' | 'sending' | 'sent' | 'error'

export function LoginRoute() {
  const { signInWithOtp } = useAuth()
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<Status>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = email.trim()
    if (!trimmed) return
    setStatus('sending')
    setErrorMsg(null)
    try {
      await signInWithOtp(trimmed)
      setStatus('sent')
    } catch (err) {
      setStatus('error')
      setErrorMsg(err instanceof Error ? err.message : '寄送登入連結失敗，請稍後再試')
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
          <Mail size={22} />
        </div>
        <h1 className="text-xl font-bold text-text-primary">登入 DawnCast</h1>
        <p className="text-sm text-text-secondary leading-relaxed">
          輸入電子郵件，我們會寄送一封登入連結給你，點擊即可登入，免設定密碼。
        </p>
      </div>

      {status === 'sent' ? (
        <div className="flex items-start gap-2.5 px-4 py-3 rounded-lg bg-success/10 border border-success/20 text-success text-sm">
          <CheckCircle2 size={18} className="shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="font-medium">登入連結已寄出</p>
            <p className="text-text-secondary text-xs leading-relaxed">
              請至 {email} 收信，點擊信中的連結完成登入。
            </p>
          </div>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <label htmlFor="login-email" className="block text-xs font-medium text-text-secondary">
              電子郵件
            </label>
            <input
              id="login-email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              className="w-full px-3 py-2.5 rounded-md bg-bg-secondary border border-border text-sm text-text-primary placeholder:text-text-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
          </div>

          {status === 'error' && errorMsg && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-danger/10 border border-danger/20 text-danger text-xs">
              <AlertCircle size={14} className="shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}

          <Button
            type="submit"
            variant="primary"
            size="lg"
            className="w-full justify-center"
            disabled={status === 'sending'}
          >
            {status === 'sending' ? (
              <>
                <Loader2 size={16} className="animate-spin" />
                寄送中
              </>
            ) : (
              <>
                <Mail size={16} />
                寄送登入連結
              </>
            )}
          </Button>
        </form>
      )}
    </div>
  )
}
