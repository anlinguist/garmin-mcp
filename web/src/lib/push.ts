import { postJSON } from './api'

export function isStandalone(): boolean {
  return window.matchMedia('(display-mode: standalone)').matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
}

export function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

export function pushSupported(): boolean {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window
}

export function registerServiceWorker(): void {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => {})
  }
}

function b64urlToBytes(s: string): Uint8Array<ArrayBuffer> {
  const b64 = (s + '='.repeat((4 - (s.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(b64)
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

/** Must be called from a tap: iOS only shows the permission prompt for a user gesture. */
export async function enablePush(publicKey: string): Promise<number> {
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') throw new Error('Notifications were not allowed.')
  const reg = await navigator.serviceWorker.ready
  const existing = await reg.pushManager.getSubscription()
  const sub = existing ?? await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: b64urlToBytes(publicKey),
  })
  const r = await postJSON<{ subscriptions: number }>('/api/push/subscribe', sub.toJSON())
  return r.subscriptions
}

export type PushTestResult = { results: { host: string; status: number | null; removed: boolean }[] }
export const sendTestPush = () => postJSON<PushTestResult>('/api/push/test')
