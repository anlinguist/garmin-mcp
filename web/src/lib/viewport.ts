// Keep the app inside the *visual* viewport. iOS doesn't resize the page when the
// keyboard opens; it shrinks and shifts the visual viewport instead. The app shell is a
// fixed frame sized and offset to match (--vv-h, --vv-top), so the header stays visible
// and the composer sits right on the keyboard. We only follow iOS here, never scroll
// the page ourselves (fighting its keyboard scrolling breaks the layout).
//
// Keyboard detection compares the visual viewport with the tallest it has been (the
// full screen), not window.innerHeight: iOS Home Screen apps shrink innerHeight with the
// keyboard too (observed: innerHeight 481 = visual height, full 894).
// Sets html.kb-open so the composer drops its home-indicator padding.
//
// Anticipation: when a text field is focused while it sits where the keyboard will be,
// iOS pans the whole page up, and our frame follows a beat later (the header visibly
// drops and snaps back). The keyboard's height is the same every time on a device, so
// we remember it, and on the tap that will open it we shrink the frame to that height
// first (gliding up with the keyboard). The field is then already above the keyboard and
// iOS has nothing to pan.
const KB_KEY = 'rabbit-kb-height'

export function trackKeyboard(): void {
  const vv = window.visualViewport
  if (!vv) return
  const root = document.documentElement
  let full = Math.max(vv.height, window.innerHeight)
  let kbGuess = 0
  try { kbGuess = Number(localStorage.getItem(KB_KEY)) || 0 } catch { /* private mode */ }
  let frame = 0
  let anticipating = 0

  const apply = () => {
    if (vv.height > full) full = vv.height
    const kb = full - vv.height
    const keyboard = kb > 120
    if (keyboard && Math.abs(kb - kbGuess) > 2) {
      kbGuess = kb
      try { localStorage.setItem(KB_KEY, String(Math.round(kb))) } catch { /* ignore */ }
    }
    // While we're anticipating, ignore the not-yet-shrunk viewport.
    if (anticipating && !keyboard) return
    root.style.setProperty('--vv-h', `${vv.height}px`)
    root.style.setProperty('--vv-top', `${vv.offsetTop}px`)
    root.classList.toggle('kb-open', keyboard)
  }
  const update = () => {
    cancelAnimationFrame(frame)
    frame = requestAnimationFrame(apply)
  }

  // The tap that will focus a text field: shrink first, so iOS doesn't pan.
  document.addEventListener('touchend', (e) => {
    const el = (e.target as Element | null)?.closest?.('textarea, input:not([type=checkbox]):not([type=radio]):not([type=file]), [contenteditable=true]')
    if (!el || el === document.activeElement || kbGuess < 120 || root.classList.contains('kb-open')) return
    root.classList.add('kb-anim')
    root.style.setProperty('--vv-h', `${full - kbGuess}px`)
    root.style.setProperty('--vv-top', '0px')
    root.classList.add('kb-open')
    window.clearTimeout(anticipating)
    // If no keyboard showed up (the tap didn't focus anything), follow iOS again.
    anticipating = window.setTimeout(() => { anticipating = 0; apply() }, 700)
    window.setTimeout(() => root.classList.remove('kb-anim'), 400)
  }, { passive: true })

  vv.addEventListener('resize', update)
  vv.addEventListener('scroll', update)
  window.addEventListener('orientationchange', () => { full = 0; kbGuess = 0; update() })
  update()
}
