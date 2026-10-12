import { useState } from 'react'

/** Google profile photo, falling back to an initial (like Dolly's header avatar). */
export function Avatar({ name, picture, size = 28 }: { name: string; picture: string; size?: number }) {
  const [broken, setBroken] = useState(false)
  const initial = (name || '?').trim().charAt(0).toUpperCase()
  if (picture && !broken) {
    return (
      <img src={picture} alt={name} width={size} height={size} referrerPolicy="no-referrer"
        onError={() => setBroken(true)} className="rounded-full border border-(--border) object-cover"
        style={{ width: size, height: size }} />
    )
  }
  return (
    <span aria-label={name} style={{ width: size, height: size }}
      className="flex items-center justify-center rounded-full bg-(--accent-subtle) text-xs font-medium text-(--accent)">
      {initial}
    </span>
  )
}
