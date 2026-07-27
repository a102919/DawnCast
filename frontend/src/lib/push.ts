// Web Push 訂閱管理。
//
// 「有沒有 PushSubscription」就是通知開關狀態——不另存偏好欄位，避免瀏覽器權限
// 與應用設定兩份真相打架。SW 的 push handler 在 public/push-sw.js。

import { api } from '../api'

const VAPID_PUBLIC_KEY = import.meta.env.VITE_VAPID_PUBLIC_KEY ?? ''

/** base64url → Uint8Array（applicationServerKey 只吃 BufferSource）。 */
export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
  const raw = atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
  // 顯式配置 ArrayBuffer：Uint8Array.from 推導出的 ArrayBufferLike 可能是
  // SharedArrayBuffer，不符合 applicationServerKey 的 BufferSource 型別。
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

/** 這個瀏覽器/情境是否支援推播。iOS Safari 只在「加到主畫面」後才為 true。 */
export function isPushSupported(): boolean {
  return (
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window &&
    VAPID_PUBLIC_KEY !== ''
  )
}

/** 目前這台裝置是否已訂閱。 */
export async function getPushEnabled(): Promise<boolean> {
  if (!isPushSupported()) return false
  const reg = await navigator.serviceWorker.ready
  return (await reg.pushManager.getSubscription()) !== null
}

/**
 * 開啟通知：要權限 → 訂閱 → 登錄到後端。
 *
 * 後端登錄失敗時把瀏覽器訂閱一併退掉，否則會留下「瀏覽器有訂閱但後端不知道」
 * 的半開狀態——UI 顯示已開啟，卻永遠收不到通知。
 */
export async function enablePush(): Promise<void> {
  if (!isPushSupported()) {
    throw new Error('這個瀏覽器不支援推播通知')
  }
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error('通知權限未開啟，請在瀏覽器設定中允許')
  }

  const reg = await navigator.serviceWorker.ready
  const existing = await reg.pushManager.getSubscription()
  const sub =
    existing ??
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
    }))

  const json = sub.toJSON()
  const keys = json.keys
  if (!keys?.p256dh || !keys.auth) {
    await sub.unsubscribe()
    throw new Error('瀏覽器未提供推播金鑰，無法開啟通知')
  }

  try {
    await api.subscribePush({
      endpoint: sub.endpoint,
      keys: { p256dh: keys.p256dh, auth: keys.auth },
    })
  } catch (err) {
    if (!existing) await sub.unsubscribe()
    throw err
  }
}

/** 關閉通知：後端先刪列，再退掉瀏覽器訂閱（順序反了會讓後端留下死 endpoint）。 */
export async function disablePush(): Promise<void> {
  if (!isPushSupported()) return
  const reg = await navigator.serviceWorker.ready
  const sub = await reg.pushManager.getSubscription()
  if (!sub) return
  await api.unsubscribePush(sub.endpoint)
  await sub.unsubscribe()
}

export type NotificationPermissionState =
  | 'default'
  | 'granted'
  | 'denied'
  | 'unsupported'

/** 讀 Notification 當前權限，不觸發 prompt。給 banner 用，enablePush 內部自己會 requestPermission。 */
export function getNotificationPermission(): NotificationPermissionState {
  if (!('Notification' in window)) return 'unsupported'
  return Notification.permission as NotificationPermissionState
}
