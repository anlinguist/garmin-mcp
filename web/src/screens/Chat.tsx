import { useEffect, useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { Avatar } from '@/components/Avatar'
import { RabbitLogo } from '@/components/RabbitLogo'
import { SettingsPanels } from '@/components/SettingsPanels'
import { Composer } from '@/components/chat/Composer'
import { MessageBubble } from '@/components/chat/MessageBubble'
import { ToolCallCard, ToolCallGroup } from '@/components/chat/ToolCallCard'
import { Button } from '@/components/ui/button'
import { postJSON, type Me } from '@/lib/api'
import { useCoach, type ChatItem, type ToolCall } from '@/hooks/useCoach'

const SUGGESTIONS = [
  'How did my training go this week?',
  'Am I recovered enough for a hard session today?',
  "How's my sleep been lately?",
  'Help me plan next week.',
]

type Row =
  | { kind: 'message'; item: Extract<ChatItem, { kind: 'message' }>; showLabel: boolean }
  | { kind: 'tool'; item: ToolCall }
  | { kind: 'group'; items: ToolCall[] }

// Consecutive tool calls collapse into one group (Dolly's ToolCallGroup); a
// speaker label shows only when the speaker changes.
function toRows(items: ChatItem[]): Row[] {
  const rows: Row[] = []
  let lastSpeaker: string | null = null
  for (let i = 0; i < items.length; i++) {
    const it = items[i]!
    if (it.kind === 'tool') {
      const run: ToolCall[] = []
      while (i < items.length && items[i]!.kind === 'tool') run.push(items[i++] as ToolCall)
      i--
      rows.push(run.length > 1 ? { kind: 'group', items: run } : { kind: 'tool', item: run[0]! })
      lastSpeaker = 'agent'
      continue
    }
    rows.push({ kind: 'message', item: it, showLabel: lastSpeaker !== it.role })
    lastSpeaker = it.role
  }
  return rows
}

export function Chat({ me, onChange }: { me: Me; onChange: () => void }) {
  const { items, status, send, clear } = useCoach(me.chat.active_turn)
  const [sheet, setSheet] = useState<'open' | 'closing' | null>(
    me.garmin.connected ? null : 'open',
  )
  const feed = useRef<HTMLDivElement>(null)
  const rows = useMemo(() => toRows(items), [items])
  const last = items[items.length - 1]
  const thinking = status === 'streaming' && !(last?.kind === 'message' && last.role === 'agent')

  // Stick to the bottom while streaming, unless the user scrolled up to read.
  useEffect(() => {
    const el = feed.current
    if (!el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 150 || status !== 'streaming') {
      el.scrollTo({ top: el.scrollHeight, behavior: status === 'streaming' ? 'instant' : 'smooth' })
    }
  }, [items, status])

  const closeSheet = () => { setSheet('closing'); setTimeout(() => setSheet(null), 200) }

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b border-(--border) px-4 pb-3 pt-[max(12px,env(safe-area-inset-top))]">
        <RabbitLogo size={26} thinking={status === 'streaming'} />
        <span className="font-display text-xl text-(--brand)">Rabbit</span>
        <button className="ml-auto rounded-full" aria-label="Settings" onClick={() => setSheet('open')}>
          <Avatar name={me.profile.name || me.user_id} picture={me.profile.picture} />
        </button>
      </header>

      {!me.garmin.connected && (
        <button onClick={() => setSheet('open')}
          className="mx-4 mt-3 rounded-(--radius) border border-(--warning)/30 bg-(--warning-subtle) px-3 py-2 text-left text-xs text-(--warning)">
          Garmin isn't connected yet. Tap to connect so Rabbit can see your training.
        </button>
      )}

      {items.length === 0 && status !== 'loading' ? (
        <div className="chat-empty flex flex-1 flex-col items-center justify-center px-6 text-center">
          <RabbitLogo size={80} className="mb-3 opacity-40" />
          <p className="mb-2 font-display text-2xl leading-tight text-(--brand)">Ready when you are.</p>
          <p className="mb-5 text-[13px] text-muted-foreground">
            Ask about your training, recovery or what to do next.
          </p>
          <div className="flex w-full max-w-[300px] flex-col gap-2">
            {SUGGESTIONS.map((s) => (
              <button key={s} disabled={!me.chat.available}
                className="w-full cursor-pointer rounded-(--radius) border border-border bg-background px-3.5 py-2.5 text-left text-[13px] text-foreground/80 transition-all hover:bg-secondary disabled:opacity-50"
                onClick={() => void send(s)}>{s}</button>
            ))}
          </div>
        </div>
      ) : (
        <div ref={feed} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4">
          <div className="mx-auto max-w-2xl space-y-4 py-4">
            {rows.map((r) => (
              <div key={r.kind === 'group' ? `g-${r.items[0]!.id}` : r.item.id}>
                {r.kind === 'message' && <MessageBubble message={r.item} showLabel={r.showLabel} />}
                {r.kind === 'tool' && <ToolCallCard toolCall={r.item} />}
                {r.kind === 'group' && <ToolCallGroup toolCalls={r.items} />}
              </div>
            ))}
            {thinking && (
              <div className="flex items-center gap-2 pl-1 text-sm text-muted-foreground">
                <div className="thinking-dots flex items-center gap-[3px]"><span /><span /><span /></div>
              </div>
            )}
          </div>
        </div>
      )}

      <Composer
        disabled={status !== 'idle' || !me.chat.available}
        onSend={(t) => void send(t)}
        placeholder={!me.chat.available ? 'Chat isn’t configured yet' : status === 'streaming' ? 'Rabbit is replying…' : 'Ask Rabbit…'}
      />

      {sheet && (
        <div className={`modal-overlay ${sheet === 'closing' ? 'modal-overlay--exit' : ''}`} onClick={closeSheet}>
          <div className={`modal ${sheet === 'closing' ? 'modal--exit' : ''}`} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">Settings</h2>
              <button className="modal-close" aria-label="Close" onClick={closeSheet}><X className="size-4" /></button>
            </div>
            <div className="modal-body overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
              <SettingsPanels me={me} onChange={onChange} onClearChat={async () => {
                try { await postJSON('/api/chat/clear'); clear() } catch (e) { window.alert((e as Error).message) }
              }} />
              {me.garmin.connected && (
                <Button variant="primary" className="mt-4 w-full" onClick={closeSheet}>Done</Button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
