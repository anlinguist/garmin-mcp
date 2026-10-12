import { useState } from 'react'
import { Clock, FlaskConical, Sunrise, Watch } from 'lucide-react'
import { Group, Icon, Row, Switch } from '@/components/settings/Grouped'
import { postJSON, putSettings, type CoachSettings, type Me } from '@/lib/api'

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

  const tz = s.timezone.split('/').pop()?.replace(/_/g, ' ') ?? s.timezone

  return (
    <>
      <Group label="Coaching messages"
        footer={`Rabbit can message you first; these arrive as notifications. Times are ${tz} time.`}>
        <Row icon={<Icon><Watch /></Icon>} title="Workout feedback" subtitle="A short note after each new activity"
          trailing={<Switch label="Workout feedback" checked={s.workout_feedback} onChange={(v) => void save({ workout_feedback: v })} />} />
        <Row icon={<Icon><Sunrise /></Icon>} title="Morning brief" subtitle="Today's plan, sleep and recovery"
          trailing={<Switch label="Morning brief" checked={s.brief_enabled} onChange={(v) => void save({ brief_enabled: v })} />} />
        {s.brief_enabled && (
          <Row icon={<Icon><Clock /></Icon>} title="Brief time"
            trailing={<input type="time" value={s.brief_time} step={300} aria-label="Brief time"
              onChange={(e) => e.target.value && void save({ brief_time: e.target.value })}
              className="rounded-lg bg-(--bg-inset) px-2.5 py-1 text-right text-[0.9412rem] text-(--text) [font-family:var(--font)]" />} />
        )}
      </Group>
      {me.dev.tools.length > 0 && (
        <Group label="Dev (fake Garmin)">
          {me.dev.tools.includes('simulate_workout') && (
            <Row icon={<Icon tone="muted"><FlaskConical /></Icon>} title="Simulate new workout" disabled={busy}
              onClick={() => void dev('simulate_workout', 'Simulated workout')} />
          )}
          {me.dev.tools.includes('brief_now') && (
            <Row icon={<Icon tone="muted"><FlaskConical /></Icon>} title="Send brief now" disabled={busy}
              onClick={() => void dev('brief_now', 'Morning brief')} />
          )}
        </Group>
      )}
      {msg && <p className="-mt-4 break-words px-4 text-[0.7647rem] text-(--text-muted)">{msg}</p>}
    </>
  )
}
