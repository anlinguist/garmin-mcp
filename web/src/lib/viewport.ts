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
export function trackKeyboard(): void {
  const vv = window.visualViewport
  if (!vv) return
  const root = document.documentElement
  let full = Math.max(vv.height, window.innerHeight)
  let frame = 0
  const update = () => {
    cancelAnimationFrame(frame)
    frame = requestAnimationFrame(() => {
      if (vv.height > full) full = vv.height
      const keyboard = full - vv.height > 120
      root.style.setProperty('--vv-h', `${vv.height}px`)
      root.style.setProperty('--vv-top', `${vv.offsetTop}px`)
      root.classList.toggle('kb-open', keyboard)
    })
  }
  vv.addEventListener('resize', update)
  vv.addEventListener('scroll', update)
  window.addEventListener('orientationchange', () => { full = 0; update() })
  update()
}
