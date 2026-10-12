// Long-press (touch) and right-click (desktop) to open a message's context menu.
// Moving the finger more than a few px cancels, so scrolling never triggers it.
import { useCallback, useRef } from 'react'

const HOLD_MS = 450
const SLOP_PX = 8

export function useLongPress(onTrigger: (el: HTMLElement) => void) {
  const timer = useRef<number | null>(null)
  const start = useRef<{ x: number; y: number } | null>(null)

  const cancel = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = null
    start.current = null
  }, [])

  const onTouchStart = useCallback((e: React.TouchEvent<HTMLElement>) => {
    const el = e.currentTarget
    const t = e.touches[0]!
    start.current = { x: t.clientX, y: t.clientY }
    timer.current = window.setTimeout(() => { timer.current = null; onTrigger(el) }, HOLD_MS)
  }, [onTrigger])

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

  return { onTouchStart, onTouchMove, onTouchEnd: cancel, onTouchCancel: cancel, onContextMenu }
}
