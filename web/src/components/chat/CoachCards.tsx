// Coach-log extras: message headers, the Discuss action, and link cards to topic threads.
import { MessageSquareText, MessagesSquare, NotebookPen, Sunrise, Watch } from 'lucide-react'
import type { Thread } from '@/lib/api'

function fmtDay(ts?: number): string {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}

export function CoachHeader({ label, ts }: { label: 'brief' | 'workout' | 'note' | null | undefined; ts?: number }) {
  if (!label) return null
  const Icon = label === 'brief' ? Sunrise : label === 'workout' ? Watch : NotebookPen
  return (
    <div className="mb-1 flex items-center gap-1.5 pl-1 text-[11px] font-semibold uppercase tracking-widest text-(--accent)">
      <Icon className="size-3.5" />
      {label === 'brief' ? 'Morning brief' : label === 'workout' ? 'Workout note' : 'Coach note'}
      <span className="font-normal normal-case tracking-normal text-muted-foreground">· {fmtDay(ts)}</span>
    </div>
  )
}

export function DiscussButton({ onClick, busy }: { onClick: () => void; busy: boolean }) {
  return (
    <button onClick={onClick} disabled={busy}
      className="mt-1 ml-1 inline-flex items-center gap-1 rounded-full border border-border bg-background px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-50">
      <MessagesSquare className="size-3" /> Discuss
    </button>
  )
}

export function ThreadLinkCard({ thread, onOpen }: { thread: Thread | undefined; onOpen: () => void }) {
  const title = thread ? thread.title || 'New chat' : 'Deleted chat'
  return (
    <button onClick={onOpen} disabled={!thread}
      className="flex w-full max-w-[85%] items-center gap-2.5 rounded-xl border border-dashed border-(--accent)/40 bg-(--accent-subtle) px-3 py-2 text-left transition-colors hover:bg-(--accent)/10 disabled:opacity-50">
      <MessageSquareText className="size-4 shrink-0 text-(--accent)" />
      <span className="min-w-0">
        <span className="block text-[10px] font-semibold uppercase tracking-widest text-(--accent)">Chat started</span>
        <span className="block truncate text-[13px] text-foreground">{title}</span>
      </span>
    </button>
  )
}
