import { useState, type ReactNode } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Link, useNavigate } from 'react-router-dom'
import { useSettings, useVocab, useAuth } from '../state'
import { api, AppError } from '../api'
import { supabase } from '../lib/supabaseClient'
import { Toggle, Chip } from '../components/primitives'
import { AlertTriangle } from 'lucide-react'
import { TOPIC_LABELS } from './episodeData'
import type { TopicKey } from './episodeData'
import { DELIVERY_TIME_OPTIONS } from '../lib/dailyOrderDate'

const TOPIC_CHOICES: readonly Exclude<TopicKey, 'all'>[] = ['tech', 'business', 'culture', 'science'] as const

function isTopicChoice(s: string): s is Exclude<TopicKey, 'all'> {
  return (TOPIC_CHOICES as readonly string[]).includes(s)
}

export function SettingsRoute() {
  const { settings, updateSettings, resetPopupPreferences } = useSettings()
  const { clearVocab, items } = useVocab()
  const { signOut, user } = useAuth()
  const navigate = useNavigate()
  const [confirmClear, setConfirmClear] = useState(false)
  const [confirmDeleteAccount, setConfirmDeleteAccount] = useState(false)
  const [isDeletingAccount, setIsDeletingAccount] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const FONT_SIZES = [
    { value: 'sm' as const, label: '小' },
    { value: 'md' as const, label: '中' },
    { value: 'lg' as const, label: '大' },
  ]

  const RATES = [0.75, 1, 1.25, 1.5] as const

  const preferredTopics = settings.preferredTopics.filter(isTopicChoice)

  const toggleTopic = (key: Exclude<TopicKey, 'all'>) => {
    const next = preferredTopics.includes(key)
      ? preferredTopics.filter(k => k !== key)
      : [...preferredTopics, key]
    void updateSettings({ preferredTopics: next })
  }

  const handleClearVocab = async () => {
    await clearVocab()
    setConfirmClear(false)
  }

  const handleSignOut = async () => {
    await signOut()
    navigate('/')
  }

  // T4 帳號自我管理：刪除本人帳號。
  // 流程：DELETE /me → supabase.auth.signOut() → localStorage.clear() → 導回首頁。
  // 任一步失敗皆 abort（避免半毀狀態；DB 已刪但前端未清會讓重複登入看到舊資料）。
  const handleDeleteAccount = async () => {
    setIsDeletingAccount(true)
    setDeleteError(null)
    try {
      await api.deleteAccount()
      try {
        await supabase.auth.signOut()
      } catch {
        // signOut 失敗不致命（DB 已清；重新整理即視為未登入）
      }
      try {
        await signOut()
      } catch {
        // 同上
      }
      localStorage.clear()
      navigate('/', { replace: true })
    } catch (err) {
      // 對外訊息：把 AppError.message 顯示給使用者；其他錯誤給通用訊息
      setDeleteError(
        err instanceof AppError ? err.message : '刪除帳號失敗，請稍後再試',
      )
      setIsDeletingAccount(false)
      setConfirmDeleteAccount(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-6 py-8">
      <h1 className="text-2xl font-semibold text-text-primary mb-8">設定</h1>

      <div className="space-y-6">
        {/* 詞卡設定 */}
        <SettingSection title="詞卡">
          <SettingRow
            label="啟用詞卡"
            description="點擊字幕單字時，顯示底部詞卡面板"
          >
            <Toggle
              checked={settings.popupEnabled}
              onChange={v => updateSettings({ popupEnabled: v })}
            />
          </SettingRow>

          <SettingRow
            label="重置詞卡偏好"
            description="清除已忽略的詞卡提示"
          >
            <button
              onClick={resetPopupPreferences}
              className="text-sm text-accent hover:underline cursor-pointer px-3 py-2 -mr-3 rounded min-h-[44px]"
            >
              重置
            </button>
          </SettingRow>
        </SettingSection>

        {/* 播放與出餐設定 */}
        <SettingSection title="播放與出餐">
          <SettingRow
            label="預設語速"
            description="調整影片播放速度"
          >
            <div className="flex gap-1.5">
              {RATES.map(rate => (
                <Chip
                  key={rate}
                  active={settings.playbackRate === rate}
                  onClick={() => updateSettings({ playbackRate: rate })}
                >
                  {rate}x
                </Chip>
              ))}
            </div>
          </SettingRow>

          <div className="px-4 py-4">
            <div className="text-sm font-medium text-text-primary">預設出餐時間</div>
            <p className="text-xs text-text-secondary mt-0.5 mb-3">
              每天點餐時預先帶入的時段，仍可在下單卡手動切換
            </p>
            <div className="flex gap-1.5 flex-wrap">
              {DELIVERY_TIME_OPTIONS.map(opt => (
                <Chip
                  key={opt.value}
                  active={settings.defaultDeliveryTime === opt.value}
                  onClick={() => updateSettings({ defaultDeliveryTime: opt.value })}
                >
                  {opt.label}
                </Chip>
              ))}
            </div>
          </div>
        </SettingSection>

        {/* 顯示設定 */}
        <SettingSection title="顯示">
          <SettingRow
            label="字幕字體大小"
            description="調整字幕區塊的字體大小"
          >
            <div className="flex gap-1.5">
              {FONT_SIZES.map(({ value, label }) => (
                <Chip
                  key={value}
                  active={settings.fontSize === value}
                  onClick={() => updateSettings({ fontSize: value })}
                >
                  {label}
                </Chip>
              ))}
            </div>
          </SettingRow>
        </SettingSection>

        {/* 學習偏好 */}
        <SettingSection title="學習偏好">
          <div className="px-4 py-4">
            <div className="text-sm font-medium text-text-primary">主題偏好</div>
            <p className="text-xs text-text-secondary mt-0.5 mb-3">
              選擇你有興趣的主題,首頁會優先推薦相關集數
            </p>
            <div className="flex gap-1.5 flex-wrap">
              {TOPIC_CHOICES.map(key => (
                <Chip
                  key={key}
                  active={preferredTopics.includes(key)}
                  onClick={() => toggleTopic(key)}
                >
                  {TOPIC_LABELS[key]}
                </Chip>
              ))}
            </div>
          </div>
        </SettingSection>

        {/* 資料 */}
        <SettingSection title="資料">
          <div>
            <SettingRow
              label="清除單字本"
              description={`目前共 ${items.length} 個單字，清除後無法復原`}
            >
              <button
                onClick={() => setConfirmClear(true)}
                disabled={items.length === 0 || confirmClear}
                className="text-sm text-danger hover:underline cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed px-3 py-2 -mr-3 rounded min-h-[44px]"
              >
                清除
              </button>
            </SettingRow>

            <AnimatePresence>
              {confirmClear && (
                <motion.div
                  key="confirm-clear"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
                  className="overflow-hidden"
                >
                  <div className="flex items-center gap-3 px-4 py-3 border-t border-border bg-bg-secondary">
                    <span className="flex items-center gap-1.5 text-xs text-danger flex-1">
                      <AlertTriangle size={14} />
                      清除後無法復原，確定要清除所有單字？
                    </span>
                    <button
                      onClick={() => setConfirmClear(false)}
                      className="text-sm text-text-secondary px-4 py-2.5 rounded-lg border border-border bg-bg-primary hover:bg-bg-secondary transition-colors min-h-[44px] min-w-[64px]"
                    >
                      取消
                    </button>
                    <button
                      onClick={handleClearVocab}
                      className="text-sm text-white font-medium px-4 py-2.5 rounded-lg bg-danger hover:opacity-90 transition-opacity min-h-[44px] min-w-[64px]"
                    >
                      確定清除
                    </button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </SettingSection>

        {/* T4 帳號自我管理：刪除帳號危險區塊。
            沿用 confirmClear 的 AnimatePresence 二次確認模式，避免引入新互動元件。 */}
        <SettingSection title="帳號">
          <div>
            {user ? (
              <SettingRow label="登入狀態" description={user.email}>
                <button
                  onClick={() => void handleSignOut()}
                  className="text-sm text-accent hover:underline cursor-pointer px-3 py-2 -mr-3 rounded min-h-[44px]"
                >
                  登出
                </button>
              </SettingRow>
            ) : (
              <SettingRow label="登入狀態" description="尚未登入">
                <Link
                  to="/login"
                  className="text-sm text-accent hover:underline cursor-pointer px-3 py-2 -mr-3 rounded min-h-[44px] inline-flex items-center"
                >
                  登入
                </Link>
              </SettingRow>
            )}

            <SettingRow
              label="刪除帳號"
              description="永久刪除帳號與所有學習資料（單字本、收藏、活動、設定、訂單）"
            >
              <button
                onClick={() => {
                  setConfirmDeleteAccount(true)
                  setDeleteError(null)
                }}
                disabled={confirmDeleteAccount || isDeletingAccount}
                className="text-sm text-danger hover:underline cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed px-3 py-2 -mr-3 rounded min-h-[44px]"
              >
                刪除帳號
              </button>
            </SettingRow>

            <AnimatePresence>
              {confirmDeleteAccount && (
                <motion.div
                  key="confirm-delete-account"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.2, ease: [0.2, 0.8, 0.2, 1] }}
                  className="overflow-hidden"
                >
                  <div className="flex flex-col gap-3 px-4 py-3 border-t border-border bg-bg-secondary">
                    <span className="flex items-center gap-1.5 text-xs text-danger">
                      <AlertTriangle size={14} />
                      此操作無法復原，將永久刪除你的帳號與所有資料。
                    </span>
                    {deleteError && (
                      <span className="text-xs text-danger" role="alert">
                        {deleteError}
                      </span>
                    )}
                    <div className="flex items-center gap-3 justify-end">
                      <button
                        onClick={() => {
                          setConfirmDeleteAccount(false)
                          setDeleteError(null)
                        }}
                        disabled={isDeletingAccount}
                        className="text-sm text-text-secondary px-4 py-2.5 rounded-lg border border-border bg-bg-primary hover:bg-bg-secondary transition-colors min-h-[44px] min-w-[64px] disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        取消
                      </button>
                      <button
                        onClick={handleDeleteAccount}
                        disabled={isDeletingAccount}
                        className="text-sm text-white font-medium px-4 py-2.5 rounded-lg bg-danger hover:opacity-90 transition-opacity min-h-[44px] min-w-[64px] disabled:opacity-40 disabled:cursor-not-allowed"
                      >
                        {isDeletingAccount ? '刪除中...' : '確定刪除我的帳號'}
                      </button>
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </SettingSection>
      </div>
    </div>
  )
}

function SettingSection({ title, children }: { readonly title: string; readonly children: ReactNode }) {
  return (
    <div>
      <h2 className="text-xs font-semibold text-text-tertiary uppercase tracking-wider mb-3">{title}</h2>
      <div className="border border-border rounded-lg divide-y divide-border bg-bg-primary">
        {children}
      </div>
    </div>
  )
}

function SettingRow({
  label,
  description,
  children,
}: {
  readonly label: string
  readonly description?: string
  readonly children: ReactNode
}) {
  return (
    <div className="flex items-start justify-between px-4 py-4 gap-3 flex-wrap">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-text-primary">{label}</div>
        {description && (
          <div className="text-xs text-text-secondary mt-0.5">{description}</div>
        )}
      </div>
      <div className="shrink-0 flex-none">{children}</div>
    </div>
  )
}
