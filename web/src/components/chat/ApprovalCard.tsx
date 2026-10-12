// Human-in-the-loop for changes to an established goal or plan. Same idea as Dolly's
// approval sheet (and its approve/reject button styles), shown inline in the chat.
import { useState } from 'react'
import { Check, ShieldQuestion, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { Approval } from '@/hooks/useCoach'

export function ApprovalCard({ item, onDecide }: {
  item: Approval
  onDecide: (approved: boolean, reason?: string) => void
}) {
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState('')
  const [sent, setSent] = useState(false)
  const pending = item.status === 'pending' && !sent
  const decide = (ok: boolean, why?: string) => { setSent(true); onDecide(ok, why) }

  return (
    <div className="w-full max-w-[92%] rounded-xl border border-(--accent)/40 bg-(--bg-surface) p-3 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <ShieldQuestion className="size-4 shrink-0 text-(--accent)" />
        <span className="text-sm font-medium">{item.summary.title}</span>
        {item.status !== 'pending' && (
          <span className={`ml-auto inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.5882rem] font-semibold uppercase tracking-wider ${
            item.status === 'approved' ? 'bg-(--success-subtle) text-(--success)' : 'bg-(--danger-subtle) text-(--danger)'}`}>
            {item.status === 'approved' ? <><Check className="size-3" /> Approved</> : <><X className="size-3" /> Declined</>}
          </span>
        )}
      </div>
      {item.summary.detail && <p className="mb-2 text-xs text-(--text-secondary)">{item.summary.detail}</p>}
      <dl className="flex flex-col gap-1.5 text-[0.7647rem]">
        {item.summary.changes.map((c, i) => (
          <div key={i} className="rounded-md bg-(--bg-inset)/60 px-2 py-1.5">
            <dt className="text-[0.5882rem] font-semibold uppercase tracking-widest text-muted-foreground">{c.field}</dt>
            <dd className="break-words">
              {c.from && <span className="text-(--text-muted) line-through decoration-(--danger)/50">{c.from}</span>}
              {c.from && <span className="px-1 text-muted-foreground">→</span>}
              <span className="text-foreground">{c.to}</span>
            </dd>
          </div>
        ))}
      </dl>
      {item.status !== 'pending' && item.reason && (
        <p className="mt-2 text-xs text-(--text-muted)">Reason: {item.reason}</p>
      )}
      {pending && !declining && (
        <div className="mt-3 flex gap-2">
          <Button variant="reject" className="flex-1" onClick={() => setDeclining(true)}>Not now</Button>
          <Button variant="approve" className="flex-1" onClick={() => decide(true)}>Approve</Button>
        </div>
      )}
      {pending && declining && (
        <div className="mt-3 flex flex-col gap-2">
          <Input className="text-base" placeholder="Why not? (optional)" value={reason}
            onChange={(e) => setReason(e.target.value)} autoFocus />
          <div className="flex gap-2">
            <Button variant="ghost" className="flex-1" onClick={() => setDeclining(false)}>Back</Button>
            <Button variant="reject" className="flex-1" onClick={() => decide(false, reason.trim())}>Decline</Button>
          </div>
        </div>
      )}
      {sent && item.status === 'pending' && <p className="mt-2 text-xs text-(--text-muted)">Sending…</p>}
    </div>
  )
}
