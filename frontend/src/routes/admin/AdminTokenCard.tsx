// 權杖設定卡：未設定時要求填入，設定後可改／清除。「權杖是否存在」的判斷
// 收斂在 AdminLayout 單一層級，這裡只管表單本身，透過 onTokenChange 回報變化。

import { useState } from 'react'
import { toast } from 'sonner'
import { Eye, EyeOff } from 'lucide-react'
import { clearAdminToken, setAdminToken } from '../../api'
import { Button, Card, SectionLabel } from '../../components/primitives'

interface AdminTokenCardProps {
  readonly token: string | null
  readonly onTokenChange: (token: string | null) => void
}

export function AdminTokenCard({ token, onTokenChange }: AdminTokenCardProps) {
  const [tokenDraft, setTokenDraft] = useState('')
  const [showToken, setShowToken] = useState(false)

  const handleSave = () => {
    const trimmed = tokenDraft.trim()
    if (!trimmed) {
      toast.error('權杖不可為空')
      return
    }
    setAdminToken(trimmed)
    onTokenChange(trimmed)
    setTokenDraft('')
    toast.success('已儲存管理員權杖')
  }

  const handleClear = () => {
    clearAdminToken()
    onTokenChange(null)
    setTokenDraft('')
    toast.success('已清除管理員權杖')
  }

  return (
    <Card className="p-4 space-y-3">
      <SectionLabel>管理員權杖</SectionLabel>
      {token ? (
        <div className="space-y-2">
          <p className="text-xs text-text-secondary">
            已設定。每次請求會帶 <code className="text-text-primary">X-Admin-Token</code> header 呼叫後端。
          </p>
          <Button size="sm" variant="ghost" onClick={handleClear}>
            清除權杖
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-text-secondary">
            從 Zeabur 後台 <code className="text-text-primary">ADMIN_TOKEN</code> 環境變數複製貼上，存於 localStorage。
          </p>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <input
                type={showToken ? 'text' : 'password'}
                value={tokenDraft}
                onChange={e => setTokenDraft(e.target.value)}
                placeholder="貼上管理員權杖"
                className="w-full px-3 py-2 pr-10 text-sm rounded-md border border-border bg-bg-primary text-text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setShowToken(s => !s)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-text-secondary hover:text-text-primary"
                aria-label={showToken ? '隱藏權杖' : '顯示權杖'}
              >
                {showToken ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <Button variant="primary" size="md" onClick={handleSave} disabled={!tokenDraft.trim()}>
              儲存
            </Button>
          </div>
        </div>
      )}
    </Card>
  )
}
