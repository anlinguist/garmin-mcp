// Goal + athlete profile setup. One active goal; starting a new one archives the old.
import { useState } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { archiveGoal, createGoal, putAthlete, updateGoal, type Athlete, type Goal } from '@/lib/api'

const DISTANCES = ['70.3', '140.6', 'Olympic triathlon', 'Sprint triathlon', 'Marathon', 'Half marathon', '10K', '5K', 'Ultra', 'Century ride', 'Other']

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-xs font-medium text-(--text-secondary)">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-(--text-muted)">{hint}</span>}
    </label>
  )
}

const area = 'min-h-[64px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-base focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring'

export function GoalSheet({ goal, athlete, onSaved, onClose, closing }: {
  goal: Goal | null; athlete: Athlete; onSaved: () => void; onClose: () => void; closing: boolean
}) {
  const [g, setG] = useState({
    title: goal?.title ?? '', event: goal?.event ?? '', date: goal?.date ?? '',
    distance: goal?.distance ?? '', target: goal?.target ?? '', priorities: goal?.priorities ?? '',
  })
  const [a, setA] = useState<Athlete>({ units: 'metric', ...athlete })
  const [startNew, setStartNew] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const editing = goal && !startNew

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setMsg(null)
    try {
      const fields = { ...g, title: g.title || g.event || g.distance || 'My goal', date: g.date || null }
      if (editing) await updateGoal(goal.id, fields)
      else {
        if (goal) await archiveGoal(goal.id)
        await createGoal(fields)
      }
      const { weekly_hours, age, ...rest } = a
      await putAthlete({ ...rest,
        weekly_hours: weekly_hours === undefined || weekly_hours === null || Number.isNaN(weekly_hours) ? null : Number(weekly_hours),
        age: age === undefined || age === null || Number.isNaN(age) ? null : Number(age) })
      onSaved()
      onClose()
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`modal-overlay ${closing ? 'modal-overlay--exit' : ''}`} onClick={onClose}>
      <div className={`modal ${closing ? 'modal--exit' : ''}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 className="modal-title">{editing ? 'Your goal' : 'What are you training for?'}</h2>
          <button className="modal-close" aria-label="Close" onClick={onClose}><X className="size-4" /></button>
        </div>
        <form onSubmit={save} className="modal-body flex flex-col gap-3 overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
          <Field label="Race or event"><Input className="text-base" placeholder="Ironman 70.3 Coeur d'Alene" value={g.event} onChange={(e) => setG({ ...g, event: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Date"><Input className="text-base" type="date" value={g.date} onChange={(e) => setG({ ...g, date: e.target.value })} /></Field>
            <Field label="Distance">
              <select className="h-9 rounded-md border border-input bg-transparent px-2 text-base" value={g.distance} onChange={(e) => setG({ ...g, distance: e.target.value })}>
                <option value="">Choose…</option>
                {DISTANCES.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Target" hint="A time, or just “finish strong”."><Input className="text-base" placeholder="sub-5:30" value={g.target} onChange={(e) => setG({ ...g, target: e.target.value })} /></Field>
          <Field label="Priorities or worries"><textarea className={area} placeholder="Swim is my weak spot; hilly bike course" value={g.priorities} onChange={(e) => setG({ ...g, priorities: e.target.value })} /></Field>

          <h3 className="mt-2 font-display text-lg text-(--brand)">About you</h3>
          <Field label="Experience"><Input className="text-base" placeholder="2 seasons of sprint tri, one marathon" value={a.experience ?? ''} onChange={(e) => setA({ ...a, experience: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Hours per week"><Input className="text-base" type="number" inputMode="decimal" min={0} max={40} value={a.weekly_hours ?? ''} onChange={(e) => setA({ ...a, weekly_hours: e.target.value === '' ? null : Number(e.target.value) })} /></Field>
            <Field label="Units">
              <select className="h-9 rounded-md border border-input bg-transparent px-2 text-base" value={a.units ?? 'metric'} onChange={(e) => setA({ ...a, units: e.target.value as Athlete['units'] })}>
                <option value="metric">Metric (km)</option><option value="imperial">Imperial (mi)</option>
              </select>
            </Field>
          </div>
          <Field label="Usual training days"><Input className="text-base" placeholder="Weekdays early, long sessions Sat/Sun" value={a.training_days ?? ''} onChange={(e) => setA({ ...a, training_days: e.target.value })} /></Field>
          <Field label="Injuries or limits"><textarea className={area} placeholder="Left knee flares with back-to-back runs" value={a.injuries ?? ''} onChange={(e) => setA({ ...a, injuries: e.target.value })} /></Field>
          <Field label="Anything else Rabbit should know"><textarea className={area} placeholder="Hate the treadmill; pool only open M/W/F" value={a.preferences ?? ''} onChange={(e) => setA({ ...a, preferences: e.target.value })} /></Field>

          {msg && <p className="text-xs text-(--danger)">{msg}</p>}
          <Button type="submit" variant="primary" className="mt-1 h-10" loading={busy}>{editing ? 'Save' : 'Save goal'}</Button>
          {goal && !startNew && (
            <button type="button" className="text-xs text-muted-foreground underline" onClick={() => {
              setStartNew(true)
              setG({ title: '', event: '', date: '', distance: '', target: '', priorities: '' })
            }}>Done with this goal? Start a new one</button>
          )}
          {startNew && <p className="text-xs text-(--text-muted)">Saving archives “{goal?.title}”. Its coach log and chats are kept.</p>}
        </form>
      </div>
    </div>
  )
}
