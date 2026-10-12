// Drag-to-dismiss for Dolly-style bottom sheets (.modal). Drag down anywhere on the
// sheet (the body only when it's scrolled to the top) and release past a threshold to
// close. The finger offset uses the CSS `translate` property, which composes with the
// sheet's transform animations: on release the sheet stays where it was let go and the
// regular exit animation (.modal--exit slide-down + overlay fade) carries it out from
// there. `onClose` must run that exit animation (not unmount immediately).
import { useCallback, useRef } from 'react'

const CLOSE_PX = 90
const CLOSE_VELOCITY = 0.5 // px per ms

export function useSheetDrag(onClose: () => void) {
  const sheet = useRef<HTMLDivElement | null>(null)
  const start = useRef<{ y: number; t: number; active: boolean } | null>(null)
  const dy = useRef(0)

  const set = (px: number, animate: boolean) => {
    const el = sheet.current
    if (!el) return
    el.style.transition = animate ? 'translate 0.2s cubic-bezier(0.16, 1, 0.3, 1)' : 'none'
    el.style.translate = px ? `0 ${px}px` : ''
  }

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const body = (e.target as HTMLElement).closest('.modal-body')
    // Inside a scrolled body the gesture scrolls content; only take over at the top.
    start.current = { y: e.touches[0]!.clientY, t: Date.now(), active: !body || body.scrollTop <= 0 }
    dy.current = 0
  }, [])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const s = start.current
    if (!s?.active) return
    const d = e.touches[0]!.clientY - s.y
    if (d <= 0) { if (dy.current) set(0, false); dy.current = 0; return }
    dy.current = d
    set(d, false)
  }, [])

  const onTouchEnd = useCallback(() => {
    const s = start.current
    start.current = null
    if (!s?.active || dy.current <= 0) return
    const v = dy.current / Math.max(1, Date.now() - s.t)
    if (dy.current > CLOSE_PX || v > CLOSE_VELOCITY) {
      onClose() // exit animation continues from the current drag position
    } else {
      set(0, true)
    }
    dy.current = 0
  }, [onClose])

  return { ref: sheet, onTouchStart, onTouchMove, onTouchEnd, onTouchCancel: onTouchEnd }
}
