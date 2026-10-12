// Long-press (touch) and right-click (desktop) to open a message's context menu.
// Moving the finger more than a few px cancels, so scrolling never triggers it.
import { useCallback, useRef } from 'react'

const HOLD_MS = 450
const SLOP_PX = 8

/** ``onRelease`` runs on the finger lift that ends a long-press. Lifting is a real user
 *  gesture to iOS, unlike the hold timer, so it's where an iOS haptic can fire. */
export function useLongPress(onTrigger: (el: HTMLElement) => void, onRelease?: () => void) {
  const timer = useRef<number | null>(null)
  const start = useRef<{ x: number; y: number } | null>(null)
  const fired = useRef(false)

  const cancel = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = null
    start.current = null
  }, [])

  const onTouchStart = useCallback((e: React.TouchEvent<HTMLElement>) => {
    const el = e.currentTarget
    const t = e.touches[0]!
    start.current = { x: t.clientX, y: t.clientY }
    fired.current = false
    timer.current = window.setTimeout(() => { timer.current = null; fired.current = true; onTrigger(el) }, HOLD_MS)
  }, [onTrigger])

  const onTouchEnd = useCallback(() => {
    if (fired.current) { fired.current = false; onRelease?.() }
    cancel()
  }, [cancel, onRelease])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const s = start.current
    const t = e.touches[0]!
    if (s && Math.hypot(t.clientX - s.x, t.clientY - s.y) > SLOP_PX) cancel()
  }, [cancel])

  const onContextMenu = useCallback((e: React.MouseEvent<HTMLElement>) => {
    e.preventDefault()
    cancel()
    onTrigger(e.currentTarget)
  }, [cancel, onTrigger])

  return { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel: cancel, onContextMenu }
}
