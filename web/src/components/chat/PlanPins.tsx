// Pinned to the top of the coach log: the plan (tap to open it) and "This week",
// which expands into planned vs. done by day. Weeks follow the plan's own weeks.
import { useState } from 'react'
import {
  Activity, Bike, Check, ChevronDown, ChevronRight, Dumbbell, Footprints, Layers, Map as MapIcon, Moon, Waves,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Goal, PlanWeek, Sport, WeekDay } from '@/lib/api'

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

function DayRow({ day, onAsk }: { day: WeekDay; onAsk: (day: WeekDay) => void }) {
  const date = new Date(`${day.date}T12:00:00`)
  const empty = !day.sessions.length && !day.extra.length
  return (
    <button onClick={() => onAsk(day)}
      className={cn('flex w-full gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-secondary',
        day.today && 'bg-(--accent-subtle)')}>
      <span className="w-9 shrink-0 pt-px text-center leading-tight">
        <span className={cn('block text-[0.6471rem] font-semibold uppercase', day.today ? 'text-(--accent)' : 'text-muted-foreground')}>{day.weekday}</span>
        <span className="block text-[0.7647rem] text-foreground/80">{date.getDate()}</span>
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        {empty && <span className="text-[0.7647rem] text-muted-foreground">Nothing planned</span>}
        {day.sessions.map((s, i) => {
          const Icon = SPORT_ICON[s.sport]
          return (
            <span key={i} className="flex items-start gap-2">
              <Icon className={cn('mt-0.5 size-3.5 shrink-0', s.status === 'done' ? 'text-(--accent)' : 'text-muted-foreground')} />
              <span className="min-w-0 flex-1">
                <span className={cn('block text-[0.7647rem] leading-snug', s.status === 'rest' ? 'text-muted-foreground' : 'text-foreground')}>
                  {s.title}{s.minutes && !hasDuration(s.title) ? <span className="text-muted-foreground"> · {fmtMinutes(s.minutes)}</span> : null}
                </span>
                {s.detail && <span className="block text-[0.6471rem] leading-snug text-muted-foreground">{s.detail}</span>}
              </span>
              <span className={cn('shrink-0 text-[0.6471rem] leading-5',
                s.status === 'done' ? 'text-(--accent)' : s.status === 'missed' ? 'text-(--danger)' : 'text-muted-foreground')}>
                {s.status === 'done' ? `✓ ${fmtMinutes(s.activity!.minutes)}` : s.status === 'missed' ? 'Missed' : ''}
              </span>
            </span>
          )
        })}
        {day.extra.map((a, i) => {
          const Icon = SPORT_ICON[a.sport]
          return (
            <span key={`x${i}`} className="flex items-start gap-2">
              <Icon className="mt-0.5 size-3.5 shrink-0 text-(--accent)" />
              <span className="min-w-0 flex-1 truncate text-[0.7647rem] text-foreground">{a.name}</span>
              <span className="shrink-0 text-[0.6471rem] leading-5 text-(--accent)">✓ {fmtMinutes(a.minutes)}</span>
            </span>
          )
        })}
      </span>
    </button>
  )
}

export function PlanPins({ goal, week, loading, onOpenPlan, onAskDay }: {
  goal: Goal
  week: PlanWeek | null
  loading: boolean
  onOpenPlan: () => void
  onAskDay: (day: WeekDay) => void
}) {
  const [open, setOpen] = useState(false)
  const t = week?.totals
  const hasPlan = !!goal.plan
  let progress = ''
  if (t && week?.plan_status === 'ready' && t.planned_sessions) {
    progress = `${t.done_sessions}/${t.planned_sessions} done`
  } else if (t) {
    const n = t.done_sessions + t.extra_sessions
    progress = n ? `${n} workout${n === 1 ? '' : 's'}` : 'No workouts yet'
  }
  const reading = loading && hasPlan && !week

  // One line: where you are in the plan and which days (falls back to "This week").
  const where = week?.index && week.index > 0
    ? `Week ${week.index}${week.weeks ? ` of ${week.weeks}` : ''}${week.phase && week.phase !== 'post-race' ? ` · ${cap(week.phase)}` : ''}`
    : 'This week'

  return (
    <>
    {open && <div className="week-dim" onClick={() => setOpen(false)} aria-hidden />}
    <div className={cn('coach-pins relative shrink-0 border-b border-(--border) bg-(--bg) px-3 py-2', open && 'z-[46]')}>
      <div className="mx-auto max-w-2xl overflow-hidden rounded-xl border border-border bg-(--bg-surface) shadow-sm">
        <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
          className="flex w-full items-center gap-2 px-3 pb-2 pt-2 text-left">
          <span className="min-w-0 flex-1">
            <span className="flex items-baseline gap-2">
              <span className="truncate text-[0.5882rem] font-semibold uppercase tracking-widest text-muted-foreground">
                {where}{week ? ` · ${fmtRange(week.start, week.end)}` : ''}
              </span>
              <span className="ml-auto shrink-0 text-[0.6471rem] text-muted-foreground">
                {reading ? 'Reading your plan…' : progress}
              </span>
            </span>
            {week ? (
              <span className="mt-1.5 flex justify-between px-1">
                {week.days.map((d) => <DayDot key={d.date} day={d} />)}
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
          <ChevronDown className={cn('size-4 shrink-0 self-center text-muted-foreground transition-transform', open && 'rotate-180')} />
        </button>
        {open && (
          <div className="max-h-[48vh] overflow-y-auto overscroll-contain border-t border-border px-1 py-1">
            <div className="flex items-center gap-2 px-2 py-1.5">
              <span className="min-w-0 flex-1 text-[0.7059rem] leading-snug text-muted-foreground">
                {hasPlan ? (week?.focus || goal.title) : 'No plan yet. Upload it or tell Rabbit about it.'}
                {t && (t.planned_minutes > 0 || t.done_minutes > 0) && (
                  <span className="block">{fmtMinutes(t.done_minutes)} done{t.planned_minutes ? ` of ~${fmtMinutes(t.planned_minutes)} planned` : ''}</span>
                )}
              </span>
              <button onClick={() => { setOpen(false); onOpenPlan() }}
                className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border px-2.5 py-1 text-[0.6471rem] text-foreground hover:bg-secondary">
                <MapIcon className="size-3.5 text-(--accent)" /> {hasPlan ? 'Open plan' : 'Add plan'} <ChevronRight className="size-3" />
              </button>
            </div>
            {week?.garmin_error && <p className="px-2 py-1 text-[0.6471rem] text-(--warning)">{week.garmin_error}</p>}
            {week?.plan_status === 'error' && <p className="px-2 py-1 text-[0.6471rem] text-muted-foreground">Couldn't read this week from your plan. Showing what you've done.</p>}
            {week?.plan_status === 'over_budget' && <p className="px-2 py-1 text-[0.6471rem] text-muted-foreground">Planned sessions show up tomorrow (today's limit is used up).</p>}
            {week?.days.map((d) => <DayRow key={d.date} day={d} onAsk={(day) => { setOpen(false); onAskDay(day) }} />)}
            {week && <p className="px-2 pb-1 pt-0.5 text-[0.5882rem] text-muted-foreground">Tap a day to ask Rabbit about it.</p>}
          </div>
        )}
      </div>
    </div>
    </>
  )
}
