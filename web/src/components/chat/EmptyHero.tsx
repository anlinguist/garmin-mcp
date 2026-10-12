// Empty-chat hero: logo + heading, with suggestion buttons hanging below. With the
// keyboard closed the whole group is centered; while typing the suggestions fade and the
// heading glides to the center (html.kb-open). Suggestions sit outside the layout, so
// nothing reflows while the keyboard animates.
import { useLayoutEffect, useRef, useState } from 'react'

export function EmptyHero({ children, suggestions, gap = 12 }: {
  children: React.ReactNode
  suggestions?: React.ReactNode
  gap?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [h, setH] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) { setH(0); return }
    const measure = () => setH(el.offsetHeight)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [suggestions != null])
  return (
    <div className="empty-hero relative flex flex-col items-center"
      style={{ ['--hero-shift' as string]: suggestions ? `${-(h + gap) / 2}px` : '0px' }}>
      {children}
      {suggestions && (
        <div ref={ref} style={{ marginTop: gap }}
          className="chat-suggestions absolute left-1/2 top-full flex w-[min(300px,calc(100vw-48px))] -translate-x-1/2 flex-col gap-2">
          {suggestions}
        </div>
      )}
    </div>
  )
}
