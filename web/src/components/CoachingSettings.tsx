import { useState } from 'react'
import { MessageCircleHeart, FlaskConical } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { postJSON, putSettings, type CoachSettings, type Me } from '@/lib/api'

function Toggle({ checked, onChange, label, hint }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; hint: string
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-3 py-1">
      <span>
        <span className="block text-sm">{label}</span>
        <span className="block text-xs text-(--text-muted)">{hint}</span>
      </span>
      <input type="checkbox" className="mt-1 size-5 shrink-0 accent-(--accent)"
        checked={checked} onChange={(e) => onChange(e.target.checked)} />
    </label>
  )
}

export function CoachingSettings({ me, onChange, onDevDone }: {
  me: Me; onChange: () => void; onDevDone: () => void
}) {
  const [s, setS] = useState<CoachSettings>(me.settings)
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save(patch: Partial<CoachSettings>) {
    const prev = s
    setS({ ...s, ...patch })
    try { setS(await putSettings(patch)); onChange() } catch (e) { setS(prev); setMsg((e as Error).message) }
  }

  async function dev(name: string, label: string) {
    setBusy(true)
    setMsg(`${label}… (Rabbit is writing it)`)
    try {
      const r = await postJSON<{ result: unknown }>(`/api/dev/${name}`)
      setMsg(`${label}: ${JSON.stringify(r.result)}`)
      onDevDone()
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="panel flex flex-col gap-2">
      <div className="flex items-start gap-3">
        <MessageCircleHeart className="mt-0.5 size-5 text-(--accent)" />
        <div>
          <h2 className="font-medium">Coaching messages</h2>
          <p className="text-sm text-(--text-secondary)">Rabbit can message you first. These arrive as notifications.</p>
        </div>
      </div>
      <Toggle checked={s.workout_feedback} onChange={(v) => void save({ workout_feedback: v })}
        label="Workout feedback" hint="A short note after each new activity syncs from Garmin." />
      <Toggle checked={s.brief_enabled} onChange={(v) => void save({ brief_enabled: v })}
        label="Morning brief" hint="Sleep, recovery and a suggestion for the day." />
      {s.brief_enabled && (
        <label className="flex items-center justify-between gap-3 py-1 text-sm">
          Brief time
          <input type="time" value={s.brief_time} step={300}
            onChange={(e) => e.target.value && void save({ brief_time: e.target.value })}
            className="rounded-md border border-(--border-strong) bg-transparent px-2 py-1 text-base" />
        </label>
      )}
      <p className="text-xs text-(--text-muted)">Time zone: {s.timezone}</p>
      {me.dev.tools.length > 0 && (
        <div className="mt-2 flex flex-col gap-2 rounded-(--radius) border border-dashed border-(--warning)/40 p-2">
          <span className="flex items-center gap-1.5 text-[0.6471rem] font-semibold uppercase tracking-widest text-(--warning)">
            <FlaskConical className="size-3.5" /> Dev (fake Garmin)
          </span>
          <div className="flex flex-wrap gap-2">
            {me.dev.tools.includes('simulate_workout') && (
              <Button disabled={busy} onClick={() => void dev('simulate_workout', 'Simulated workout')}>Simulate new workout</Button>
            )}
            {me.dev.tools.includes('brief_now') && (
              <Button disabled={busy} onClick={() => void dev('brief_now', 'Morning brief')}>Send brief now</Button>
            )}
          </div>
        </div>
      )}
      {msg && <p className="break-words text-xs text-(--text-muted)">{msg}</p>}
    </section>
  )
}
