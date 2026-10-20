// Pinned to the top of the coach log: the plan (tap to open it) and "This week",
// which expands into planned vs. done by day. Weeks follow the plan's own weeks.
// With the card open, swiping sideways goes to earlier or later weeks.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import {
  Activity, ArrowLeftRight, Bike, Check, MessageCircle, ChevronRight, Dumbbell, Footprints, Layers, Map as MapIcon, Moon, RotateCcw, Undo2, Waves,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { getPlanWeek, type Goal, type PlannedSession, type PlanWeek, type Sport, type WeekDay } from '@/lib/api'

const SPORT_ICON: Record<Sport, typeof Bike> = {
  swim: Waves, bike: Bike, run: Footprints, brick: Layers, strength: Dumbbell, rest: Moon, other: Activity,
}

export function fmtMinutes(m: number): string {
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  const r = m % 60
  return r ? `${h}h ${r}m` : `${h}h`
}

function fmtRange(start: string, end: string): string {
  const a = new Date(`${start}T12:00:00`)
  const b = new Date(`${end}T12:00:00`)
  const mon = (d: Date) => d.toLocaleDateString([], { month: 'short' })
  return a.getMonth() === b.getMonth()
    ? `${mon(a)} ${a.getDate()}–${b.getDate()}`
    : `${mon(a)} ${a.getDate()} – ${mon(b)} ${b.getDate()}`
}

const hasDuration = (title: string) => /\d+\s*(['’]|min\b|h\b)/i.test(title)

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** "Week 3 of 39 · Base" (whatever is known). */
export function planLine(week: PlanWeek | null, goal: Goal): string {
  const bits: string[] = []
  if (week?.index && week.index > 0) bits.push(`Week ${week.index}${week.weeks ? ` of ${week.weeks}` : ''}`)
  if (week?.phase && week.phase !== 'post-race') bits.push(cap(week.phase))
  if (!bits.length && week?.weeks_to_race != null && week.weeks_to_race >= 1) bits.push(`${week.weeks_to_race} wks to race`)
  if (!bits.length) bits.push(goal.title)
  return bits.join(' · ')
}

type DayState = 'done' | 'partial' | 'missed' | 'planned' | 'rest' | 'extra' | 'empty'

function dayState(d: WeekDay): DayState {
  const work = d.sessions.filter((s) => s.status !== 'rest')
  if (work.length) {
    const done = work.filter((s) => s.status === 'done').length
    if (done === work.length) return 'done'
    if (done > 0) return 'partial'
    return work.some((s) => s.status === 'missed') ? 'missed' : 'planned'
  }
  if (d.extra.length) return 'extra'
  return d.sessions.length ? 'rest' : 'empty'
}

function DayDot({ day }: { day: WeekDay }) {
  const st = dayState(day)
  return (
    <span className="flex flex-col items-center gap-1">
      <span className={cn('text-[0.5882rem] leading-none', day.today ? 'font-semibold text-(--accent)' : 'text-muted-foreground')}>
        {day.weekday.charAt(0)}
      </span>
      <span className={cn('flex size-[18px] items-center justify-center rounded-full border text-white',
        day.today && 'ring-2 ring-(--accent)/30 ring-offset-1 ring-offset-(--bg-surface)',
        st === 'done' || st === 'extra' ? 'border-(--accent) bg-(--accent)'
          : st === 'partial' ? 'border-(--accent) bg-(--accent)/45'
          : st === 'missed' ? 'border-(--danger)/50 bg-(--danger)/10'
          : st === 'planned' ? 'border-(--border-strong) bg-transparent'
          : 'border-transparent bg-(--bg-elevated)')}>
        {(st === 'done' || st === 'extra') && <Check className="size-3" strokeWidth={3} />}
      </span>
    </span>
  )
}

type Item = { key: string; sport: Sport; title: string; minutes: number | null; detail: string
  status: 'done' | 'missed' | 'planned' | 'rest' | 'extra'; doneMinutes?: number; session?: PlannedSession }

function items(day: WeekDay): Item[] {
  return [
    ...day.sessions.map((s, i) => ({ key: `s${i}`, sport: s.sport, title: s.title, minutes: s.minutes,
      detail: s.detail, status: s.status, doneMinutes: s.activity?.minutes, session: s })),
    ...day.extra.map((a, i) => ({ key: `x${i}`, sport: a.sport, title: a.name, minutes: null,
      detail: a.distance_km ? `${a.distance_km} km` : '', status: 'extra' as const, doneMinutes: a.minutes })),
  ]
}

/** One day: a line per session; tapping a session opens its details and an Ask button.
 *  Only the Ask button starts a chat, so opening and closing details never does. */
function DayRow({ day, openKey, onToggle, onAsk, onReset }: {
  day: WeekDay; openKey: string | null
  onToggle: (key: string | null) => void
  onAsk: (day: WeekDay, session?: PlannedSession) => void
  onReset: (date: string) => void
}) {
  const date = new Date(`${day.date}T12:00:00`)
  const list = items(day)
  return (
    <div className={cn('flex gap-3 px-2 py-1.5', list.length <= 1 && 'items-center', day.today && 'bg-(--accent-subtle)')}>
      <span className={cn('w-9 shrink-0 text-center leading-tight', list.length > 1 && 'pt-1')}>
        <span className={cn('block text-[0.6471rem] font-semibold uppercase', day.today ? 'text-(--accent)' : 'text-muted-foreground')}>{day.weekday}</span>
        <span className="block text-[0.7647rem] text-foreground/80">{date.getDate()}</span>
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        {!list.length && <span className="py-1 text-[0.7647rem] text-muted-foreground">Nothing planned</span>}
        {list.map((it) => {
          const Icon = SPORT_ICON[it.sport]
          const id = `${day.date}:${it.key}`
          const expanded = openKey === id
          const changed = it.session?.changed
          const canOpen = it.status !== 'rest' || !!changed
          const dur = it.minutes && !hasDuration(it.title) ? fmtMinutes(it.minutes) : ''
          return (
            <span key={it.key} className="flex flex-col">
              <button type="button" disabled={!canOpen} aria-expanded={canOpen ? expanded : undefined}
                onClick={() => onToggle(expanded ? null : id)}
                className="flex w-full items-center gap-2 py-1 text-left disabled:cursor-default">
                <Icon className={cn('size-3.5 shrink-0', it.status === 'done' || it.status === 'extra' ? 'text-(--accent)' : 'text-muted-foreground')} />
                <span className={cn('min-w-0 flex-1 truncate text-[0.7647rem]', it.status === 'rest' ? 'text-muted-foreground' : 'text-foreground')}>
                  {it.title}{dur && <span className="text-muted-foreground"> · {dur}</span>}
                </span>
                {changed && <ArrowLeftRight className="size-3 shrink-0 text-(--accent)" aria-label="Changed from the plan" />}
                <span className={cn('shrink-0 text-[0.6471rem]',
                  it.status === 'done' || it.status === 'extra' ? 'text-(--accent)' : it.status === 'missed' ? 'text-(--danger)' : 'text-muted-foreground')}>
                  {it.status === 'done' || it.status === 'extra' ? `✓ ${fmtMinutes(it.doneMinutes ?? 0)}` : it.status === 'missed' ? 'Missed' : ''}
                </span>
              </button>
              {canOpen && (
                <span className={cn('session-detail', expanded && 'session-detail--open')}>
                  <span className="min-h-0 overflow-hidden">
                    <span className="flex flex-col gap-2 pb-2 pl-5.5">
                      {it.detail && <span className="text-[0.7059rem] leading-snug text-muted-foreground">{it.detail}</span>}
                      {changed && (
                        <span className="text-[0.7059rem] leading-snug text-(--accent)">
                          Changed: {changed}. The plan had {it.session?.was}.
                        </span>
                      )}
                      <span className="flex flex-wrap gap-2">
                        <button type="button" tabIndex={expanded ? 0 : -1}
                          onClick={() => onAsk(day, it.session)}
                          className="inline-flex w-fit items-center gap-1.5 rounded-full bg-(--accent-subtle) px-2.5 py-1 text-[0.6471rem] font-medium text-(--accent) active:opacity-70">
                          <MessageCircle className="size-3.5" /> Ask Rabbit
                        </button>
                        {changed && (
                          <button type="button" tabIndex={expanded ? 0 : -1}
                            onClick={() => onReset(day.date)}
                            className="inline-flex w-fit items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[0.6471rem] font-medium text-muted-foreground active:opacity-70">
                            <Undo2 className="size-3.5" /> Back to the plan
                          </button>
                        )}
                      </span>
                    </span>
                  </span>
                </span>
              )}
            </span>
          )
        })}
      </span>
    </div>
  )
}

/** "Next week", "3 weeks ago" (for weeks the plan doesn't number). */
function relWeek(offset: number): string {
  if (offset === 0) return 'This week'
  if (offset === 1) return 'Next week'
  if (offset === -1) return 'Last week'
  return offset > 0 ? `In ${offset} weeks` : `${-offset} weeks ago`
}

const SWIPE_MIN = 60 // px, or a quick flick

export function PlanPins({ goal, week, loading, onOpenPlan, onAskDay, onResetDay }: {
  goal: Goal
  week: PlanWeek | null
  loading: boolean
  onOpenPlan: () => void
  onAskDay: (day: WeekDay, session?: PlannedSession) => void
  /** undo a day change; resolves with that week as it is now */
  onResetDay?: (date: string, offset: number) => Promise<PlanWeek | void>
}) {
  const [open, setOpenState] = useState(false)
  const [openKey, setOpenKey] = useState<string | null>(null)
  // Other weeks, fetched as the card is swiped to them. This week comes from the parent;
  // when it reloads (a day changed in chat, a new plan) the others may be stale too.
  const [offset, setOffset] = useState(0)
  const [others, setOthers] = useState<Record<number, PlanWeek>>({})
  const [othersLoading, setOthersLoading] = useState(false)
  useEffect(() => { setOthers({}) }, [week])
  useEffect(() => {
    if (offset === 0 || others[offset]) return
    let live = true
    setOthersLoading(true)
    getPlanWeek(offset).then((w) => { if (live) setOthers((o) => ({ ...o, [offset]: w })) }, () => {})
      .finally(() => { if (live) setOthersLoading(false) })
    return () => { live = false }
  }, [offset, others])
  const shown = offset === 0 ? week : others[offset] ?? null
  const shownLoading = offset === 0 ? loading : othersLoading
  // The drawer may use all the room below the card (down to the safe area).
  const cardRef = useRef<HTMLDivElement>(null)
  const [room, setRoom] = useState<number | null>(null)
  // Stay above the dimming until the drawer has finished sliding shut.
  const [raised, setRaised] = useState(false)
  const lowerTimer = useRef<number | null>(null)
  // The card is one rounded box (header + drawer) floating over the feed; this spacer keeps
  // the header's height in the layout so opening the drawer never pushes the feed.
  const headRef = useRef<HTMLButtonElement>(null)
  const [headH, setHeadH] = useState<number | null>(null)
  useLayoutEffect(() => {
    const el = headRef.current
    if (!el) return
    const measure = () => setHeadH(el.offsetHeight + 2) // + the card's 1px borders
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  const setOpen = (next: boolean | ((v: boolean) => boolean)) => {
    const v = typeof next === 'function' ? next(open) : next
    if (lowerTimer.current) window.clearTimeout(lowerTimer.current)
    if (v) {
      setRaised(true)
      const bottom = headRef.current?.getBoundingClientRect().bottom
      if (bottom != null) setRoom(Math.max(200, (window.visualViewport?.height ?? window.innerHeight) - bottom - 16))
    }
    else lowerTimer.current = window.setTimeout(() => {
      setRaised(false)
      setOffset(0) // closed, the card is always this week
      setOpenKey(null)
    }, 340)
    setOpenState(v)
  }

  // Sideways swipe on the open card: the week's dots and days follow the finger, then
  // slide out and the next week slides in from the other side.
  const headSlide = useRef<HTMLSpanElement>(null)
  const daysSlide = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const swiped = useRef(false) // a swipe that ends on the header isn't a tap
  const can = useRef({ prev: false, next: false })
  can.current = { prev: !!shown?.prev, next: !!shown?.next }
  const go = (dir: 1 | -1, from = 0) => {
    const els = [headSlide.current, daysSlide.current].filter(Boolean) as HTMLElement[]
    const w = cardRef.current?.offsetWidth ?? 320
    const ease = 'cubic-bezier(0.2, 0.8, 0.2, 1)'
    const out = els.map((el) => el.animate(
      [{ transform: `translateX(${from}px)`, opacity: 1 }, { transform: `translateX(${-dir * w * 0.6}px)`, opacity: 0 }],
      { duration: 150, easing: 'ease-in', fill: 'forwards' }))
    void Promise.all(out.map((a) => a.finished)).then(() => {
      flushSync(() => { setOffset((o) => o + dir); setOpenKey(null) })
      if (scroller.current) scroller.current.scrollTop = 0
      els.forEach((el) => { el.style.transform = '' })
      out.forEach((a) => a.cancel())
      els.forEach((el) => el.animate(
        [{ transform: `translateX(${dir * w * 0.6}px)`, opacity: 0 }, { transform: 'translateX(0)', opacity: 1 }],
        { duration: 220, easing: ease }))
    }, () => {})
  }
  const goRef = useRef(go)
  goRef.current = go
  useEffect(() => {
    const card = cardRef.current
    if (!card || !open) return
    let x0 = 0, y0 = 0, t0 = 0, dx = 0, mode: 'none' | 'x' | 'y' = 'none'
    const els = () => [headSlide.current, daysSlide.current].filter(Boolean) as HTMLElement[]
    const start = (e: TouchEvent) => {
      if (e.touches.length !== 1) { mode = 'y'; return }
      x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; t0 = e.timeStamp; dx = 0; mode = 'none'
    }
    const move = (e: TouchEvent) => {
      if (mode === 'y') return
      const mx = e.touches[0].clientX - x0
      const my = e.touches[0].clientY - y0
      if (mode === 'none') {
        if (Math.abs(mx) < 10 && Math.abs(my) < 10) return
        mode = Math.abs(mx) > Math.abs(my) * 1.2 ? 'x' : 'y'
        if (mode === 'y') return
      }
      e.preventDefault() // no vertical scroll while swiping sideways
      const ok = mx < 0 ? can.current.next : can.current.prev
      dx = ok ? mx : mx / 4 // resist where there are no more weeks
      els().forEach((el) => { el.style.transform = `translateX(${dx}px)` })
    }
    const end = (e: TouchEvent) => {
      if (mode !== 'x') return
      mode = 'none'
      swiped.current = true
      window.setTimeout(() => { swiped.current = false }, 50)
      const dir = dx < 0 ? 1 : -1
      const fast = Math.abs(dx) / Math.max(1, e.timeStamp - t0) > 0.4
      if ((Math.abs(dx) > SWIPE_MIN || fast) && (dir === 1 ? can.current.next : can.current.prev)) {
        goRef.current(dir, dx)
      } else {
        els().forEach((el) => {
          el.animate([{ transform: `translateX(${dx}px)` }, { transform: 'translateX(0)' }],
            { duration: 180, easing: 'ease-out' })
          el.style.transform = ''
        })
      }
    }
    card.addEventListener('touchstart', start, { passive: true })
    card.addEventListener('touchmove', move, { passive: false })
    card.addEventListener('touchend', end)
    card.addEventListener('touchcancel', end)
    return () => {
      card.removeEventListener('touchstart', start)
      card.removeEventListener('touchmove', move)
      card.removeEventListener('touchend', end)
      card.removeEventListener('touchcancel', end)
    }
  }, [open])
  useEffect(() => () => {
    if (lowerTimer.current) window.clearTimeout(lowerTimer.current)
  }, [])
  const hasPlan = !!goal.plan
  const reading = shownLoading && hasPlan && !shown

  // One line: where you are in the plan and which days (falls back to "This week").
  const where = shown?.index && shown.index > 0
    ? `Week ${shown.index}${shown.weeks ? ` of ${shown.weeks}` : ''}${shown.phase && shown.phase !== 'post-race' ? ` · ${cap(shown.phase)}` : ''}`
    : relWeek(offset)

  return (
    <>
    <div className={cn('week-dim', open && 'week-dim--open')} onClick={() => setOpen(false)} aria-hidden />
    <div className="coach-pins shrink-0 border-b border-(--border) bg-(--bg) px-3 pb-2.5 pt-0">
      {/* Only the card rises above the dimming; the drawer slides down over the feed. */}
      <div className={cn('relative mx-auto max-w-2xl', raised && 'z-[46]')} style={headH ? { height: headH } : undefined}>
      <div ref={cardRef} className={cn('overflow-hidden rounded-xl border border-border bg-(--bg-surface) transition-shadow duration-300',
        headH && 'absolute inset-x-0 top-0', raised ? 'shadow-lg' : 'shadow-sm')}>
        <button ref={headRef} onClick={() => { if (!swiped.current) setOpen((v) => !v) }} aria-expanded={open}
          className="flex w-full items-center gap-2 px-3 pb-2 pt-2 text-left">
          <span ref={headSlide} className="min-w-0 flex-1">
            <span className="flex items-baseline gap-2">
              <span className="truncate text-[0.5882rem] font-semibold uppercase tracking-widest text-muted-foreground">
                {where}{shown ? ` · ${fmtRange(shown.start, shown.end)}` : ''}
              </span>
              {reading && <span className="ml-auto shrink-0 text-[0.6471rem] text-muted-foreground">Reading your plan…</span>}
            </span>
            {shown ? (
              <span className="mt-1.5 flex justify-between px-1">
                {shown.days.map((d) => <DayDot key={d.date} day={d} />)}
              </span>
            ) : (
              <span className="mt-1.5 flex justify-between px-1">
                {Array.from({ length: 7 }, (_, i) => (
                  <span key={i} className="flex flex-col items-center gap-1">
                    <span className="h-2.5 w-2 rounded bg-(--bg-elevated)" />
                    <span className="size-[18px] animate-pulse rounded-full bg-(--bg-elevated)" />
                  </span>
                ))}
              </span>
            )}
          </span>
        </button>
        <div className={cn('week-drawer', open && 'week-drawer--open')} aria-hidden={!open} inert={!open}>
          <div className="min-h-0 overflow-hidden">
          <div ref={scroller} className="overflow-y-auto overscroll-contain border-t border-(--border) pb-1"
            style={{ maxHeight: room ? `calc(${room}px - env(safe-area-inset-bottom))` : '52vh' }}>
            <button onClick={() => { setOpen(false); onOpenPlan() }}
              className="flex w-full items-center gap-3 border-b border-(--border) px-3 py-2.5 text-left active:bg-(--bg-inset)">
              <span className="flex size-[30px] shrink-0 items-center justify-center rounded-lg bg-(--accent-subtle) text-(--accent)">
                <MapIcon className="size-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[0.8235rem] font-medium text-foreground">{hasPlan ? 'Your plan' : 'Add your plan'}</span>
                <span className="block truncate text-[0.6471rem] text-muted-foreground">
                  {hasPlan ? (shown?.focus?.replace(/\s*\([^)]*\)\s*$/, '') || goal.title) : 'Upload it or tell Rabbit about it'}
                </span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            </button>
            <div ref={daysSlide}>
            {/* Swiping is touch-only; these do the same for keyboards and screen readers. */}
            {shown?.prev && <button type="button" onClick={() => go(-1)} className="sr-only">Previous week</button>}
            {shown?.next && <button type="button" onClick={() => go(1)} className="sr-only">Next week</button>}
            {offset !== 0 && (
              <div className="flex justify-center pt-2">
                <button type="button" onClick={() => setOffset(0)}
                  className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-1 text-[0.6471rem] font-medium text-muted-foreground active:opacity-70">
                  <RotateCcw className="size-3" /> Back to this week
                </button>
              </div>
            )}
            {shown?.garmin_error && <p className="px-2 py-1 text-[0.6471rem] text-(--warning)">{shown.garmin_error}</p>}
            {shown?.plan_status === 'error' && <p className="px-2 py-1 text-[0.6471rem] text-muted-foreground">Couldn't read this week from your plan.{shown.start <= shown.today ? " Showing what you've done." : ''}</p>}
            {shown?.plan_status === 'over_budget' && <p className="px-2 py-1 text-[0.6471rem] text-muted-foreground">Planned sessions show up tomorrow (today's limit is used up).</p>}
            {!shown && reading && <p className="px-2 py-3 text-center text-[0.6471rem] text-muted-foreground">Reading your plan…</p>}
            <div className="divide-y divide-(--border)">
              {shown?.days.map((d) => <DayRow key={d.date} day={d} openKey={openKey} onToggle={setOpenKey}
                onAsk={(day, session) => { setOpen(false); onAskDay(day, session) }}
                onReset={(date) => {
                  setOpenKey(null)
                  const at = offset
                  void onResetDay?.(date, at).then((w) => {
                    if (w && at !== 0) setOthers((o) => ({ ...o, [at]: w }))
                  })
                }} />)}
            </div>
            </div>
          </div>
          </div>
        </div>
      </div>
      </div>
    </div>
    </>
  )
}
