// Long-press on a "Chat started" card in the coach log: an iMessage-style peek at how the
// chat opened (tap it to open the chat), with Open and Delete underneath.
import { useEffect, useState } from 'react'
import { ExternalLink, MessageSquareText, Trash2 } from 'lucide-react'
import { getJSON, type Thread } from '@/lib/api'

type Turn = { id: string; kind: string; user: string | null; parts: { type: string; text?: string }[] }

const PREVIEW_CHARS = 600

export function ThreadPeek({ thread, onOpen, onDelete, onClose }: {
  thread: Thread
  onOpen: () => void
  onDelete: () => Promise<void>
  onClose: () => void
}) {
  const [turns, setTurns] = useState<Turn[] | null>(null)
  const [failed, setFailed] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    getJSON<{ turns: Turn[] }>(`/api/threads/${thread.id}/messages?from=start&limit=3`)
      .then((r) => setTurns(r.turns), () => setFailed(true))
  }, [thread.id])
  // Release the page-wide selection lock that the long-press set.
  useEffect(() => () => {
    window.setTimeout(() => document.documentElement.classList.remove('msg-menu-open'), 50)
  }, [])

  const first = turns?.find((t) => t.kind === 'chat' && t.user)
  const seed = turns?.find((t) => t.kind === 'seed')
  const reply = first?.parts.filter((p) => p.type === 'text').map((p) => p.text).join('').trim() ?? ''
  const seedText = seed?.parts.filter((p) => p.type === 'text').map((p) => p.text).join('').trim() ?? ''
  const started = new Date(thread.created * 1000).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })

  return (
    <>
      <div className="msg-menu-backdrop" onClick={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }} />
      <div className="thread-peek" onClick={onClose}>
        <button className="thread-peek-card" onClick={(e) => { e.stopPropagation(); onOpen() }}>
          <span className="flex items-center gap-2 border-b border-(--border) px-3.5 py-2.5">
            <MessageSquareText className="size-4 shrink-0 text-(--accent)" />
            <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-foreground">{thread.title || 'New chat'}</span>
            <span className="shrink-0 text-[11px] text-muted-foreground">{started}</span>
          </span>
          <span className="block max-h-[46vh] space-y-2.5 overflow-hidden px-3.5 py-3 text-left">
            {!turns && !failed && <span className="thinking-dots flex gap-[3px]"><span /><span /><span /></span>}
            {failed && <span className="text-[13px] text-muted-foreground">Couldn't load this chat.</span>}
            {seedText && (
              <span className="block rounded-lg border border-dashed border-border px-2.5 py-1.5 text-[12px] leading-snug text-muted-foreground">
                From your coach log: {clip(seedText, 160)}
              </span>
            )}
            {first?.user && (
              <span className="ml-auto block w-fit max-w-[88%] whitespace-pre-wrap rounded-xl rounded-tr-sm border border-black/10 bg-[var(--msg-user-bg)] px-3 py-2 text-[14px] leading-relaxed text-[var(--msg-user-text)]">
                {clip(first.user, PREVIEW_CHARS)}
              </span>
            )}
            {reply && (
              <span className="block max-w-[92%] whitespace-pre-wrap rounded-xl rounded-tl-sm border border-border bg-background px-3 py-2 text-[13px] leading-relaxed text-foreground/85">
                {clip(reply, PREVIEW_CHARS)}
              </span>
            )}
            {turns && !first && !failed && <span className="text-[13px] text-muted-foreground">No messages yet.</span>}
          </span>
          <span className="block border-t border-(--border) py-1.5 text-center text-[11px] text-muted-foreground">Tap to open</span>
        </button>
        <div role="menu" className="msg-menu thread-peek-menu" onClick={(e) => e.stopPropagation()}>
          <button role="menuitem" onClick={onOpen}
            className="flex w-full items-center justify-between gap-6 px-4 py-2.5 text-left text-[15px] text-(--text) active:bg-(--bg-inset)">
            Open chat<ExternalLink className="size-4 text-(--text-secondary)" />
          </button>
          <button role="menuitem" disabled={deleting}
            onClick={async () => {
              if (!confirming) { setConfirming(true); return }
              setDeleting(true)
              try { await onDelete() } finally { setDeleting(false) }
            }}
            className="flex w-full items-center justify-between gap-6 px-4 py-2.5 text-left text-[15px] text-(--danger) active:bg-(--bg-inset) disabled:opacity-50">
            {deleting ? 'Deleting…' : confirming ? 'Tap again to delete' : 'Delete chat'}
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>
    </>
  )
}

function clip(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n).trimEnd()}…` : s
}
