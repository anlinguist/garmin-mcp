// Native-feeling drag-to-dismiss for Dolly-style bottom sheets (.modal inside
// .modal-overlay).
//
// * Drag down anywhere on the sheet (inside .modal-body only once it's scrolled to the
//   top). The backdrop dims in step with the sheet.
// * On release, the decision uses the finger's velocity over the last ~80 ms, not the
//   whole gesture: a flick down dismisses, a flick up snaps back, otherwise it dismisses
//   past a third of the sheet's height.
// * Dismissal continues at the release speed off the bottom of the screen with the
//   backdrop fading in sync, then calls `onDismissed`, which should unmount the sheet
//   immediately (no second exit animation).
//
// The offset uses the CSS `translate` property, which composes with the sheet's
// slide-up `transform` animation instead of fighting it.
import { useCallback, useRef } from 'react'

const FLICK = 0.45      // px/ms
const SAMPLE_MS = 80
const SNAP = 'cubic-bezier(0.16, 1, 0.3, 1)'

type Sample = { y: number; t: number }

export function useSheetDrag(onDismissed: () => void) {
  const ref = useRef<HTMLDivElement | null>(null)
  const g = useRef<{ startY: number; active: boolean; samples: Sample[]; dy: number } | null>(null)
  const done = useRef(false)

  const overlay = () => ref.current?.parentElement as HTMLElement | null | undefined

  const apply = (dy: number, ms = 0, ease = SNAP) => {
    const el = ref.current
    const ov = overlay()
    if (!el) return
    const h = el.offsetHeight || window.innerHeight
    el.style.transition = ms ? `translate ${ms}ms ${ease}` : 'none'
    el.style.translate = dy ? `0 ${dy}px` : ''
    if (ov) {
      // The overlay's fade-in animation pins `opacity`, so dim its background color instead.
      const k = Math.max(0, Math.min(1, dy / h))
      ov.style.transition = ms ? `background-color ${ms}ms ${ease}` : 'none'
      ov.style.backgroundColor = dy ? `rgba(0, 0, 0, ${(0.4 * (1 - k)).toFixed(3)})` : ''
    }
  }

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    if (done.current) return
    const body = (e.target as HTMLElement).closest('.modal-body')
    const y = e.touches[0]!.clientY
    g.current = { startY: y, active: !body || body.scrollTop <= 0, samples: [{ y, t: performance.now() }], dy: 0 }
  }, [])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const s = g.current
    if (!s?.active || done.current) return
    const y = e.touches[0]!.clientY
    const now = performance.now()
    s.samples.push({ y, t: now })
    while (s.samples.length > 2 && now - s.samples[0]!.t > SAMPLE_MS) s.samples.shift()
    // Rubber-band a little above the resting position instead of a hard stop.
    const raw = y - s.startY
    s.dy = raw >= 0 ? raw : -Math.min(24, Math.sqrt(-raw) * 2)
    apply(s.dy)
  }, [])

  const onTouchEnd = useCallback(() => {
    const s = g.current
    g.current = null
    const el = ref.current
    if (!s?.active || !el || done.current) return
    if (s.dy === 0) return
    const first = s.samples[0]!
    const last = s.samples[s.samples.length - 1]!
    // A finger that paused before lifting has no velocity, whatever it did earlier.
    const v = performance.now() - last.t > SAMPLE_MS ? 0
      : (last.y - first.y) / Math.max(1, last.t - first.t) // + is downward
    const h = el.offsetHeight || window.innerHeight
    const dismiss = v > FLICK || (v > -FLICK / 2 && s.dy > h / 3)
    if (!dismiss) {
      apply(0, 320)
      return
    }
    done.current = true
    const remaining = Math.max(0, window.innerHeight - el.getBoundingClientRect().top)
    const ms = Math.round(Math.min(320, Math.max(140, remaining / Math.max(v, 1.2))))
    apply(s.dy + remaining, ms, 'cubic-bezier(0.3, 0.7, 0.4, 1)')
    window.setTimeout(() => { done.current = false; onDismissed() }, ms)
  }, [onDismissed])

  return { ref, onTouchStart, onTouchMove, onTouchEnd, onTouchCancel: onTouchEnd }
}
