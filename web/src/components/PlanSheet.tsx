// The plan, opened from the coach log's pinned plan bar: where you are in it, Rabbit's
// summary, the original files, and a way to ask about it. Edits live in "What Rabbit knows".
import { useSheetDrag } from '@/hooks/useSheetDrag'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, MessageCircle, Pencil, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ATTACH_INPUT_ID } from '@/components/chat/Composer'
import { planLine } from '@/components/chat/PlanPins'
import { attachmentUrl, formatFileSize } from '@/lib/attachments'
import type { Goal, PlanWeek } from '@/lib/api'

export function PlanSheet({ goal, week, onAsk, onUploadPlan, onEdit, onClose, onDismissed, closing }: {
  goal: Goal; week: PlanWeek | null
  onAsk: () => void; onUploadPlan: () => void; onEdit: () => void
  onClose: () => void; onDismissed: () => void; closing: boolean
}) {
  const drag = useSheetDrag(onDismissed)
  const files = goal.plan_files ?? []
  const raceDay = goal.date
    ? new Date(`${goal.date}T12:00:00`).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })
    : null

  return (
    <div className={`modal-overlay ${closing ? 'modal-overlay--exit' : ''}`} onClick={onClose}>
      <div className={`modal ${closing ? 'modal--exit' : ''}`} onClick={(e) => e.stopPropagation()} {...drag}>
        <div className="sheet-handle" />
        <div className="modal-header">
          <h2 className="modal-title">Your plan</h2>
          <button className="modal-close" aria-label="Close" onClick={onClose}><X className="size-4" /></button>
        </div>
        <div className="modal-body flex flex-col gap-3 overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
          <section className="panel flex flex-col gap-1">
            <p className="font-display text-lg leading-tight text-(--brand)">{goal.title}</p>
            {raceDay && <p className="text-xs text-muted-foreground">{raceDay}</p>}
            <p className="text-sm text-foreground">{planLine(week, goal)}</p>
            {week?.focus && <p className="text-xs text-muted-foreground">{week.focus}</p>}
          </section>

          <div className="flex gap-2">
            <Button variant="primary" className="flex-1 gap-1.5" onClick={onAsk}>
              <MessageCircle className="size-4" /> Ask about my plan
            </Button>
            {goal.plan && (
              <Button variant="ghost" size="icon" aria-label="Edit plan" onClick={onEdit}>
                <Pencil className="size-4" />
              </Button>
            )}
          </div>

          {files.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {files.map((f) => (
                <a key={f.id} href={attachmentUrl(f.id)} target="_blank" rel="noopener"
                  className="inline-flex items-center gap-1.5 rounded border border-border bg-secondary px-2 py-1 text-xs">
                  <FileText className="size-3 text-accent" />
                  <span className="max-w-[180px] truncate">{f.name}</span>
                  <span className="font-mono text-[10px] text-muted-foreground">{formatFileSize(f.size)}</span>
                </a>
              ))}
            </div>
          )}

          {goal.plan ? (
            <section className="panel">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Rabbit's summary</p>
              <div className="prose prose-sm max-w-none text-foreground prose-p:my-1 prose-li:my-0">
                <Markdown remarkPlugins={[remarkGfm]}>{goal.plan}</Markdown>
              </div>
            </section>
          ) : (
            <p className="text-sm text-(--text-muted)">
              No plan yet. Upload it (a PDF or screenshots) or describe it to Rabbit, and this week's sessions will show up in your coach log.
            </p>
          )}

          <Button asChild variant="ghost" className="self-start">
            <label htmlFor={ATTACH_INPUT_ID} onClick={onUploadPlan}>{goal.plan ? 'Send an updated plan' : 'Upload my plan'}</label>
          </Button>
        </div>
      </div>
    </div>
  )
}
