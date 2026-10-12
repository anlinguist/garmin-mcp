// Best-effort light haptic tap.
// * Android & co: the Vibration API.
// * iOS Safari has no Vibration API. Toggling a native `<input type="checkbox" switch>`
//   (Safari 17.4+) fires the Taptic Engine, so we click a hidden one through its label.
//   Undocumented, the same trick as the `ios-haptics` package; it may stop working in a
//   future iOS, in which case this silently does nothing.
let label: HTMLLabelElement | null = null

function iosSwitch(): HTMLLabelElement {
  if (label?.isConnected) return label
  label = document.createElement('label')
  label.setAttribute('aria-hidden', 'true')
  label.style.cssText = 'position:fixed;left:-9999px;top:0;width:1px;height:1px;overflow:hidden;opacity:0;pointer-events:none'
  const input = document.createElement('input')
  input.type = 'checkbox'
  input.setAttribute('switch', '')
  input.tabIndex = -1
  label.appendChild(input)
  document.body.appendChild(label)
  return label
}

export function hapticTap(): void {
  try {
    if (typeof navigator.vibrate === 'function' && navigator.vibrate(10)) return
    iosSwitch().click()
  } catch {
    /* no haptics on this device */
  }
}
