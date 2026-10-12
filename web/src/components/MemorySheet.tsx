// "What Rabbit knows": a readable view of the goal, plan, profile and coach notes that
// Rabbit picked up in conversation. Nothing to fill in up front: you tell Rabbit, and fix
// any section here (inline) or by asking Rabbit to change it.
import { useSheetDrag } from '@/hooks/useSheetDrag'
import { useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, MessageCircle, Pencil, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { goalCountdown, putAthlete, updateGoal, type Athlete, type Goal } from '@/lib/api'
import { attachmentUrl, formatFileSize } from '@/lib/attachments'
import { ATTACH_INPUT_ID } from '@/components/chat/Composer'

const area = 'min-h-[96px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-base focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring'

function Section({ title, onEdit, onTell, children }: {
  title: string; onEdit?: () => void; onTell?: () => void; children: React.ReactNode
}) {
  return (
    <section className="panel flex flex-col gap-2">
      <div className="flex items-center gap-1">
        <h3 className="flex-1 font-medium">{title}</h3>
        {onTell && (
          <Button variant="ghost" size="sm" className="h-7 gap-1 px-2 text-[0.6875rem]" onClick={onTell}>
            <MessageCircle className="size-3.5" /> Tell Rabbit
          </Button>
        )}
        {onEdit && (
          <Button variant="ghost" size="icon" className="size-7" aria-label={`Edit ${title}`} onClick={onEdit}>
            <Pencil className="size-3.5" />
          </Button>
        )}
      </div>
      {children}
    </section>
  )
}

const Empty = ({ children }: { children: React.ReactNode }) => (
  <p className="text-sm text-(--text-muted)">{children}</p>
)

const ATHLETE_LABELS: [keyof Athlete, string][] = [
  ['experience', 'Experience'], ['weekly_hours', 'Hours per week'], ['training_days', 'Training days'],
  ['injuries', 'Injuries or limits'], ['preferences', 'Preferences'], ['units', 'Units'], ['age', 'Age'],
]

export function MemorySheet({ goal, athlete, onChanged, onTell, onUploadPlan, onClose, onDismissed, closing }: {
  goal: Goal | null; athlete: Athlete
  onChanged: () => void
  onTell: (draft: string) => void
  onUploadPlan: () => void
  onClose: () => void; onDismissed: () => void; closing: boolean
}) {
  const [editing, setEditing] = useState<'goal' | 'plan' | 'about' | 'notes' | null>(null)
  const [g, setG] = useState({ title: goal?.title ?? '', date: goal?.date ?? '', target: goal?.target ?? '', priorities: goal?.priorities ?? '' })
  const [plan, setPlan] = useState(goal?.plan ?? '')
  const [notes, setNotes] = useState(goal?.notes ?? '')
  const [a, setA] = useState<Athlete>(athlete)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [showFullPlan, setShowFullPlan] = useState(false)
  const drag = useSheetDrag(onDismissed)

  async function save(fn: () => Promise<unknown>) {
    setBusy(true); setMsg(null)
    try { await fn(); setEditing(null); onChanged() } catch (e) { setMsg((e as Error).message) } finally { setBusy(false) }
  }
  const saveBar = (onSave: () => void) => (
    <div className="flex gap-2">
      <Button variant="primary" loading={busy} onClick={onSave}>Save</Button>
      <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
    </div>
  )
  const countdown = goalCountdown(goal)
  const athleteRows = ATHLETE_LABELS.filter(([k]) => athlete[k] !== undefined && athlete[k] !== null && athlete[k] !== '')

  return (
    <div className={`modal-overlay ${closing ? 'modal-overlay--exit' : ''}`} onClick={onClose}>
      <div className={`modal ${closing ? 'modal--exit' : ''}`} onClick={(e) => e.stopPropagation()} {...drag}>
        <div className="sheet-handle" />
        <div className="modal-header">
          <h2 className="modal-title">What Rabbit knows</h2>
          <button className="modal-close" aria-label="Close" onClick={onClose}><X className="size-4" /></button>
        </div>
        <div className="modal-body flex flex-col gap-3 overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
          <p className="text-xs text-(--text-muted)">
            Rabbit fills this in as you chat. Tell it about changes anytime, or fix anything here.
          </p>

          <Section title="Goal" onTell={() => onTell(goal ? 'Update my goal: ' : "I'm training for ")}
            onEdit={goal ? () => setEditing('goal') : undefined}>
            {editing === 'goal' && goal ? (
              <div className="flex flex-col gap-2">
                <Input className="text-base" placeholder="Title" value={g.title} onChange={(e) => setG({ ...g, title: e.target.value })} />
                <Input className="text-base" type="date" value={g.date ?? ''} onChange={(e) => setG({ ...g, date: e.target.value })} />
                <Input className="text-base" placeholder="Target" value={g.target} onChange={(e) => setG({ ...g, target: e.target.value })} />
                <textarea className={area} placeholder="Priorities or worries" value={g.priorities} onChange={(e) => setG({ ...g, priorities: e.target.value })} />
                {saveBar(() => void save(() => updateGoal(goal.id, { ...g, date: g.date || null })))}
              </div>
            ) : goal ? (
              <div className="text-sm">
                <p className="font-medium">{goal.title}</p>
                {(goal.event || goal.distance) && goal.event !== goal.title && <p className="text-(--text-secondary)">{[goal.event, goal.distance].filter(Boolean).join(' · ')}</p>}
                {goal.date && <p className="text-(--text-secondary)">{new Date(`${goal.date}T12:00:00`).toLocaleDateString([], { dateStyle: 'medium' })}{countdown ? ` · ${countdown}` : ''}</p>}
                {goal.target && <p className="text-(--text-secondary)">Target: {goal.target}</p>}
                {goal.priorities && <p className="text-(--text-secondary)">{goal.priorities}</p>}
              </div>
            ) : (
              <Empty>Not set yet. Tell Rabbit what you're training for.</Empty>
            )}
          </Section>

          {goal && (
            <Section title="Your plan" onEdit={goal.plan ? () => setEditing('plan') : undefined}>
              {editing === 'plan' ? (
                <>
                  <textarea className={`${area} min-h-[220px] font-mono text-[0.8125rem]`} value={plan} onChange={(e) => setPlan(e.target.value)} />
                  {saveBar(() => void save(() => updateGoal(goal.id, { plan })))}
                </>
              ) : goal.plan ? (
                <div className={`prose prose-sm max-w-none overflow-hidden text-foreground prose-p:my-1 prose-li:my-0 ${showFullPlan ? '' : 'max-h-48'}`}>
                  <Markdown remarkPlugins={[remarkGfm]}>{goal.plan}</Markdown>
                </div>
              ) : (
                <Empty>No plan yet. Upload it (a PDF or screenshots) or just describe it to Rabbit.</Empty>
              )}
              {goal.plan && editing !== 'plan' && goal.plan.length > 280 && (
                <button className="self-start text-xs text-(--accent)" onClick={() => setShowFullPlan((v) => !v)}>
                  {showFullPlan ? 'Show less' : 'Show all'}
                </button>
              )}
              {(goal.plan_files ?? []).length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {goal.plan_files!.map((f) => (
                    <a key={f.id} href={attachmentUrl(f.id)} target="_blank" rel="noopener"
                      className="inline-flex items-center gap-1.5 rounded border border-border bg-secondary px-2 py-1 text-xs">
                      <FileText className="size-3 text-accent" />
                      <span className="max-w-[140px] truncate">{f.name}</span>
                      <span className="font-mono text-[0.625rem] text-muted-foreground">{formatFileSize(f.size)}</span>
                    </a>
                  ))}
                </div>
              )}
              <Button asChild className="self-start">
                <label htmlFor={ATTACH_INPUT_ID} onClick={onUploadPlan}>{goal.plan ? 'Send an updated plan' : 'Upload my plan'}</label>
              </Button>
            </Section>
          )}

          <Section title="About you" onTell={() => onTell('Something Rabbit should know about me: ')}
            onEdit={() => setEditing('about')}>
            {editing === 'about' ? (
              <div className="flex flex-col gap-2">
                <Input className="text-base" placeholder="Experience" value={a.experience ?? ''} onChange={(e) => setA({ ...a, experience: e.target.value })} />
                <div className="grid grid-cols-2 gap-2">
                  <Input className="text-base" type="number" inputMode="decimal" placeholder="Hours / week" value={a.weekly_hours ?? ''} onChange={(e) => setA({ ...a, weekly_hours: e.target.value === '' ? null : Number(e.target.value) })} />
                  <select className="h-9 rounded-md border border-input bg-transparent px-2 text-base" value={a.units ?? 'metric'} onChange={(e) => setA({ ...a, units: e.target.value as Athlete['units'] })}>
                    <option value="metric">Metric</option><option value="imperial">Imperial</option>
                  </select>
                </div>
                <Input className="text-base" placeholder="Training days" value={a.training_days ?? ''} onChange={(e) => setA({ ...a, training_days: e.target.value })} />
                <textarea className={area} placeholder="Injuries or limits" value={a.injuries ?? ''} onChange={(e) => setA({ ...a, injuries: e.target.value })} />
                <textarea className={area} placeholder="Preferences" value={a.preferences ?? ''} onChange={(e) => setA({ ...a, preferences: e.target.value })} />
                {saveBar(() => void save(() => putAthlete(a)))}
              </div>
            ) : athleteRows.length ? (
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                {athleteRows.map(([k, label]) => (
                  <div key={k} className="contents">
                    <dt className="text-(--text-muted)">{label}</dt><dd className="text-(--text-secondary)">{String(athlete[k])}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <Empty>Nothing yet. Rabbit picks this up as you chat.</Empty>
            )}
          </Section>

          {goal && (
            <Section title="Coach notes" onEdit={() => setEditing('notes')}>
              {editing === 'notes' ? (
                <>
                  <textarea className={area} value={notes} onChange={(e) => setNotes(e.target.value)} />
                  {saveBar(() => void save(() => updateGoal(goal.id, { notes })))}
                </>
              ) : goal.notes ? (
                <p className="whitespace-pre-wrap text-sm text-(--text-secondary)">{goal.notes}</p>
              ) : (
                <Empty>What Rabbit learns during this block (how sessions felt, niggles, what works).</Empty>
              )}
            </Section>
          )}

          {msg && <p className="text-xs text-(--danger)">{msg}</p>}
          {goal && (
            <button className="mt-1 text-xs text-muted-foreground underline"
              onClick={() => onTell("I'm starting a new goal: ")}>
              Training for something new? Tell Rabbit
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
