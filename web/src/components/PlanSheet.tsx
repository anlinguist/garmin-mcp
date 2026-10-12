// The plan, opened from the coach log's pinned plan bar: where you are in it, Rabbit's
// summary, the original files, and a way to ask about it. Edits live in "What Rabbit knows".
import { useSheetDrag } from '@/hooks/useSheetDrag'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { FileText, MessageCircle, Pencil, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Group, Icon } from '@/components/settings/Grouped'
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
        <div className="modal-body settings flex flex-col gap-5 overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
          <div className="px-1">
            <p className="font-display text-[1.2941rem] leading-tight text-(--brand)">{goal.title}</p>
            <p className="mt-1 text-[0.7647rem] text-(--text-muted)">
              {[raceDay, planLine(week, goal)].filter(Boolean).join(' · ')}
            </p>
          </div>

          <div className="flex gap-2">
            <Button variant="primary" className="h-10 flex-1 gap-1.5" onClick={onAsk}>
              <MessageCircle className="size-4" /> Ask about my plan
            </Button>
            {goal.plan && (
              <Button variant="outline" size="icon" className="size-10" aria-label="Edit plan" onClick={onEdit}>
                <Pencil className="size-4" />
              </Button>
            )}
          </div>

          {files.length > 0 && (
            <Group label="Plan files">
              {files.map((f) => (
                <a key={f.id} href={attachmentUrl(f.id)} target="_blank" rel="noopener"
                  className="flex items-center gap-3 px-4 py-2.5 active:bg-(--bg-inset)">
                  <Icon><FileText /></Icon>
                  <span className="min-w-0 flex-1 truncate text-[0.8824rem] text-(--text)">{f.name}</span>
                  <span className="shrink-0 text-[0.7059rem] text-(--text-muted)">{formatFileSize(f.size)}</span>
                </a>
              ))}
            </Group>
          )}

          <Group label="Rabbit's summary"
            footer={<label htmlFor={ATTACH_INPUT_ID} onClick={onUploadPlan} className="cursor-pointer text-(--accent)">
              {goal.plan ? 'Send an updated plan' : 'Upload my plan'}</label>}>
            {goal.plan ? (
              <div className="chat-text prose prose-sm text-[0.9412rem]! max-w-none px-4 py-3 text-foreground prose-headings:mb-1 prose-headings:mt-3 first:prose-headings:mt-0 prose-headings:font-sans prose-headings:text-[0.7059rem] prose-headings:font-semibold prose-headings:uppercase prose-headings:tracking-widest prose-headings:text-muted-foreground prose-p:my-1 prose-li:my-0.5 prose-ul:my-1 prose-ul:pl-4">
                <Markdown remarkPlugins={[remarkGfm]}>{goal.plan}</Markdown>
              </div>
            ) : (
              <p className="px-4 py-3 text-[0.8235rem] text-(--text-muted)">
                No plan yet. Upload it (a PDF or screenshots) or describe it to Rabbit, and this week's sessions will show up in your coach log.
              </p>
            )}
          </Group>
        </div>
      </div>
    </div>
  )
}
