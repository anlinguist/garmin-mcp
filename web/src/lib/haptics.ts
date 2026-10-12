// Light haptic tap via the Vibration API (Android and other browsers that support it).
// iOS Safari has no Vibration API, and the hidden-<input switch> workaround no longer
// fires the Taptic Engine on current iOS, so on iPhone this is a no-op.
export function hapticTap(): void {
  try {
    navigator.vibrate?.(10)
  } catch {
    /* no haptics on this device */
  }
}
