// Flags the on-screen keyboard (html.kb-open) using the visual viewport, so the composer
// can drop its home-indicator padding while typing (the keyboard covers that area).
// Deliberately does NOT resize the app or touch scrolling: fighting iOS's own keyboard
// scrolling breaks the layout.
export function trackKeyboard(): void {
  const vv = window.visualViewport
  if (!vv) return
  const update = () => {
    document.documentElement.classList.toggle('kb-open', window.innerHeight - vv.height > 120)
  }
  vv.addEventListener('resize', update)
  update()
}
