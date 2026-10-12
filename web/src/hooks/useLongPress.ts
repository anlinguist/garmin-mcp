// Long-press (touch) and right-click (desktop) to open a message's context menu.
//
// On iOS the haptic tick you feel on a long-press comes from the system's own text
// selection starting. Web pages can't trigger haptics, so instead of fighting that
// gesture we ride it: bubbles stay selectable (with the native callout disabled and an
// invisible selection highlight), and when iOS starts a selection inside the bubble
// being pressed, we clear it and open our menu. The tick lands exactly as the menu
// appears. Our own timer is the fallback (non-text targets, other platforms), and runs a
// little later so the native path wins when it can. Moving the finger cancels.
import { useCallback, useEffect, useRef } from 'react'

const HOLD_MS = 600
const SLOP_PX = 8

export function useLongPress(onTrigger: (el: HTMLElement) => void) {
  const timer = useRef<number | null>(null)
  const start = useRef<{ x: number; y: number } | null>(null)
  const pressed = useRef<HTMLElement | null>(null)
  const trigger = useRef(onTrigger)
  trigger.current = onTrigger

  const cancel = useCallback(() => {
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = null
    start.current = null
    pressed.current = null
  }, [])

  const settleUntil = useRef(0)

  const fire = useCallback((el: HTMLElement) => {
    cancel()
    // Lock selection page-wide right away (before the finger lifts); MessageMenu removes it.
    document.documentElement.classList.add('msg-menu-open')
    document.getSelection()?.removeAllRanges()
    settleUntil.current = performance.now() + 1500 // swallow the rest of the native gesture
    trigger.current(el)
  }, [cancel])

  // The native long-press: iOS begins selecting text inside the pressed bubble.
  useEffect(() => {
    const onSelection = () => {
      const sel = document.getSelection()
      if (!sel || sel.isCollapsed || !sel.anchorNode) return
      const el = pressed.current
      if (el && el.contains(sel.anchorNode)) {
        sel.removeAllRanges()
        fire(el)
        return
      }
      // Just after opening: the finger may still be extending iOS's selection in a bubble.
      const node = sel.anchorNode.nodeType === 1 ? sel.anchorNode as Element : sel.anchorNode.parentElement
      if (performance.now() < settleUntil.current && node?.closest('.msg-press')) sel.removeAllRanges()
    }
    document.addEventListener('selectionchange', onSelection)
    return () => document.removeEventListener('selectionchange', onSelection)
  }, [fire])

  const onTouchStart = useCallback((e: React.TouchEvent<HTMLElement>) => {
    const el = e.currentTarget
    const t = e.touches[0]!
    start.current = { x: t.clientX, y: t.clientY }
    pressed.current = el
    timer.current = window.setTimeout(() => { timer.current = null; if (pressed.current === el) fire(el) }, HOLD_MS)
  }, [fire])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const s = start.current
    const t = e.touches[0]!
    if (s && Math.hypot(t.clientX - s.x, t.clientY - s.y) > SLOP_PX) cancel()
  }, [cancel])

  const onContextMenu = useCallback((e: React.MouseEvent<HTMLElement>) => {
    e.preventDefault()
    fire(e.currentTarget)
  }, [fire])

  // The lift that ends a menu-opening press: iOS shows its Copy bar here, sometimes after
  // reselecting the word under the finger. Cancel the default and clear any selection now
  // and shortly after, so the outcome doesn't depend on which side of the race we land.
  const onTouchEnd = useCallback((e: React.TouchEvent) => {
    const opened = performance.now() < settleUntil.current
    cancel()
    if (!opened) return
    if (e.cancelable) e.preventDefault()
    const clear = () => document.getSelection()?.removeAllRanges()
    clear()
    window.setTimeout(clear, 50)
    window.setTimeout(clear, 300)
  }, [cancel])

  return { onTouchStart, onTouchMove, onTouchEnd, onTouchCancel: cancel, onContextMenu }
}
