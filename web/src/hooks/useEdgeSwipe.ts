// iOS-style "swipe from the left edge to go back". The screen follows the finger and the
// previous view (`under`, drawn beneath it) slides in from a quarter-width to the left, as
// in iOS. Let go past a third of the width (or with a quick flick) and both finish the move,
// then `onBack` runs in the same frame the transforms reset, so the real previous view takes
// over exactly where the drawn one was. Anything else snaps back.
//
// Only touches that start within EDGE_PX of the left edge count, and only once the motion
// is clearly sideways, so scrolling and long-presses elsewhere are untouched. Listeners are
// native (not React's) because touchmove has to be non-passive to stop the feed scrolling
// mid-swipe. Installed web apps have no system back gesture, so the edge is ours.
import { useEffect, useRef } from 'react'
import { flushSync } from 'react-dom'

const EDGE_PX = 24
const DECIDE_PX = 10
const COMMIT_FRACTION = 0.33
const FLICK_PX_PER_MS = 0.5
const PARALLAX = 0.25
const EASE = 'cubic-bezier(0.2, 0.9, 0.25, 1)'

export function useEdgeSwipe(
  ref: React.RefObject<HTMLElement | null>,
  underRef: React.RefObject<HTMLElement | null>,
  { enabled, onBack }: { enabled: boolean; onBack: () => void },
) {
  const back = useRef(onBack)
  back.current = onBack

  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    let start: { x: number; y: number } | null = null
    let swiping = false
    let last = { x: 0, t: 0 }
    let v = 0

    /** Place the screen `x` px to the right; the view underneath tracks it. */
    const place = (x: number, transition: string) => {
      const under = underRef.current
      const width = el.offsetWidth || 1
      el.style.transition = transition
      el.style.transform = x ? `translateX(${x}px)` : ''
      el.style.boxShadow = x ? '-8px 0 24px rgba(0,0,0,0.14)' : ''
      if (under) {
        const p = Math.min(1, x / width)
        under.style.transition = transition
        under.style.visibility = 'visible' // hidden again by clear(), once the screen covers it
        under.style.transform = `translateX(${-PARALLAX * width * (1 - p)}px)`
        under.style.filter = `brightness(${0.92 + 0.08 * p})`
      }
    }
    const clear = () => {
      el.style.transition = el.style.transform = el.style.boxShadow = ''
      const under = underRef.current
      if (under) {
        under.style.transition = under.style.transform = under.style.filter = ''
        under.style.visibility = 'hidden'
      }
    }

    const onStart = (e: TouchEvent) => {
      const t = e.touches[0]
      if (e.touches.length !== 1 || !t || t.clientX > EDGE_PX) return
      start = { x: t.clientX, y: t.clientY }
      last = { x: t.clientX, t: e.timeStamp }
      v = 0
    }
    const onMove = (e: TouchEvent) => {
      const t = e.touches[0]
      if (!start || !t) return
      const dx = t.clientX - start.x, dy = t.clientY - start.y
      if (!swiping) {
        if (Math.hypot(dx, dy) < DECIDE_PX) return
        if (dx <= 0 || Math.abs(dy) > Math.abs(dx)) { start = null; return } // a scroll, not a swipe
        swiping = true
      }
      e.preventDefault()
      const dt = e.timeStamp - last.t
      if (dt > 0) v = (t.clientX - last.x) / dt
      last = { x: t.clientX, t: e.timeStamp }
      place(Math.max(0, dx), 'none')
    }
    const onEnd = () => {
      const s = start, was = swiping
      start = null
      swiping = false
      if (!s || !was) return
      const width = el.offsetWidth
      const commit = last.x - s.x > width * COMMIT_FRACTION || v > FLICK_PX_PER_MS
      const ms = reduced ? 0 : commit ? 220 : 200
      place(commit ? width : 0, `transform ${ms}ms ${EASE}, filter ${ms}ms ${EASE}`)
      window.setTimeout(() => {
        if (commit) flushSync(() => back.current()) // the real view is in place before paint
        clear()
      }, ms)
    }

    el.addEventListener('touchstart', onStart, { passive: true })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd)
    el.addEventListener('touchcancel', onEnd)
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onEnd)
    }
  }, [ref, underRef, enabled])
}
