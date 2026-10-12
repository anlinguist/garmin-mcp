import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Menu, MessageSquarePlus, Target, X } from 'lucide-react'
import { Avatar } from '@/components/Avatar'
import { GoalSheet } from '@/components/GoalSheet'
import { RabbitLogo } from '@/components/RabbitLogo'
import { SettingsPanels } from '@/components/SettingsPanels'
import { ThreadsSheet } from '@/components/ThreadsSheet'
import { CoachHeader, DiscussButton, ThreadLinkCard } from '@/components/chat/CoachCards'
import { Composer } from '@/components/chat/Composer'
import { MessageBubble } from '@/components/chat/MessageBubble'
import { ToolCallCard, ToolCallGroup } from '@/components/chat/ToolCallCard'
import { Button } from '@/components/ui/button'
import {
  createThread, deleteThread, goalCountdown, listThreads, putSettings,
  type Goal, type Me, type Thread,
} from '@/lib/api'
import { useCoach, type ChatItem, type ChatMessage, type ThreadLink, type ToolCall } from '@/hooks/useCoach'

const COACH_SUGGESTIONS = [
  'How did my training go this week?',
  'Am I recovered enough for a hard session today?',
  "How's my sleep been lately?",
]
const TOPIC_SUGGESTIONS = ['Help me plan next week.', 'Can I move my long ride to Sunday?', 'What should I eat before a long run?']

type Row =
  | { kind: 'message'; item: ChatMessage; showLabel: boolean }
  | { kind: 'tool'; item: ToolCall }
  | { kind: 'group'; items: ToolCall[] }
  | { kind: 'link'; item: ThreadLink }

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
    if (it.kind === 'link') { rows.push({ kind: 'link', item: it }); lastSpeaker = null; continue }
    rows.push({ kind: 'message', item: it, showLabel: lastSpeaker !== it.role && !it.label })
    lastSpeaker = it.role
  }
  return rows
}

type Sheet = 'settings' | 'threads' | 'goal'

export function Chat({ me, onChange }: { me: Me; onChange: () => void }) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [goal, setGoal] = useState<Goal | null>(me.goal)
  const [threadId, setThreadId] = useState<string | null>(null)
  const { items, status, send, stop, reload } = useCoach(threadId, me.chat.active_turn)
  const [sheet, setSheet] = useState<Sheet | null>(me.garmin.connected ? null : 'settings')
  const [closing, setClosing] = useState(false)
  const [busy, setBusy] = useState(false)
  const feed = useRef<HTMLDivElement>(null)

  const thread = threads.find((t) => t.id === threadId) ?? null
  const byId = useMemo(() => Object.fromEntries(threads.map((t) => [t.id, t])), [threads])
  const rows = useMemo(() => toRows(items), [items])
  const last = items[items.length - 1]
  const thinking = status === 'streaming' && !(last?.kind === 'message' && last.role === 'agent')

  const refreshThreads = useCallback(async (select?: string) => {
    const r = await listThreads()
    setThreads(r.threads)
    setGoal(r.goal)
    setThreadId((cur) => select ?? (cur && r.threads.some((t) => t.id === cur) ? cur : r.threads[0]?.id ?? null))
  }, [])

  useEffect(() => { void refreshThreads() }, [refreshThreads])
  // After a reply in an untitled topic thread, its title (from the first message) appears.
  useEffect(() => { if (status === 'idle' && thread?.kind === 'topic' && !thread.title) void refreshThreads() },
    [status]) // eslint-disable-line react-hooks/exhaustive-deps

  // First run: record the phone's time zone so the morning brief lands at local time.
  useEffect(() => {
    if (me.settings.timezone_set) return
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (tz) void putSettings({ timezone: tz }).then(onChange).catch(() => {})
  }, [me.settings.timezone_set]) // eslint-disable-line react-hooks/exhaustive-deps

  // Stick to the bottom while streaming, unless the user scrolled up to read.
  useEffect(() => {
    const el = feed.current
    if (!el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 150 || status !== 'streaming') {
      el.scrollTo({ top: el.scrollHeight, behavior: status === 'streaming' ? 'instant' : 'smooth' })
    }
  }, [items, status])

  const open = (s: Sheet) => { setClosing(false); setSheet(s) }
  const close = () => { setClosing(true); setTimeout(() => { setSheet(null); setClosing(false) }, 200) }

  async function newThread(from?: { thread_id: string; turn_id: string }) {
    setBusy(true)
    try {
      const t = await createThread(from)
      await refreshThreads(t.id)
      if (sheet) close()
    } catch (e) {
      window.alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const isCoach = thread?.kind === 'coach'
  const suggestions = isCoach ? COACH_SUGGESTIONS : TOPIC_SUGGESTIONS
  const countdown = goalCountdown(goal)

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b border-(--border) px-3 pb-2.5 pt-[max(10px,env(safe-area-inset-top))]">
        <Button variant="ghost" size="icon" aria-label="Chats" onClick={() => open('threads')}><Menu className="size-5" /></Button>
        <button className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => open('threads')}>
          <RabbitLogo size={24} thinking={status === 'streaming'} className="shrink-0" />
          <span className="min-w-0">
            <span className="block truncate font-display text-lg leading-tight text-(--brand)">
              {isCoach || !thread ? 'Rabbit' : thread.title || 'New chat'}
            </span>
            <span className="block truncate text-[11px] leading-tight text-muted-foreground">
              {goal ? `${goal.title}${countdown ? ` · ${countdown}` : ''}` : 'No goal set'}
              {isCoach ? ' · Coach log' : ''}
            </span>
          </span>
        </button>
        <Button variant="ghost" size="icon" aria-label="New chat" disabled={busy} onClick={() => void newThread()}>
          <MessageSquarePlus className="size-5" />
        </Button>
        <button className="rounded-full" aria-label="Settings" onClick={() => open('settings')}>
          <Avatar name={me.profile.name || me.user_id} picture={me.profile.picture} />
        </button>
      </header>

      {!me.garmin.connected && (
        <button onClick={() => open('settings')}
          className="mx-4 mt-3 rounded-(--radius) border border-(--warning)/30 bg-(--warning-subtle) px-3 py-2 text-left text-xs text-(--warning)">
          Garmin isn't connected yet. Tap to connect so Rabbit can see your training.
        </button>
      )}
      {!goal && isCoach && (
        <button onClick={() => open('goal')}
          className="mx-4 mt-3 flex items-center gap-2 rounded-(--radius) border border-(--accent)/30 bg-(--accent-subtle) px-3 py-2 text-left text-xs text-(--accent)">
          <Target className="size-4 shrink-0" />
          What are you training for? Set a goal so briefs and feedback fit your plan.
        </button>
      )}

      {items.length === 0 && status !== 'loading' ? (
        <div className="chat-empty flex flex-1 flex-col items-center justify-center px-6 text-center">
          <RabbitLogo size={80} className="mb-3 opacity-40" />
          <p className="mb-2 font-display text-2xl leading-tight text-(--brand)">
            {isCoach ? 'Your coach log' : 'Ready when you are.'}
          </p>
          <p className="mb-5 text-[13px] text-muted-foreground">
            {isCoach ? 'Morning briefs and workout notes land here. Ask anything about your training.'
              : 'Ask about your training, recovery or what to do next.'}
          </p>
          <div className="flex w-full max-w-[300px] flex-col gap-2">
            {suggestions.map((s) => (
              <button key={s} disabled={!me.chat.available}
                className="w-full cursor-pointer rounded-(--radius) border border-border bg-background px-3.5 py-2.5 text-left text-[13px] text-foreground/80 transition-all hover:bg-secondary disabled:opacity-50"
                onClick={() => void send(s)}>{s}</button>
            ))}
          </div>
        </div>
      ) : (
        <div ref={feed} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4">
          <div className="mx-auto max-w-2xl space-y-4 py-4">
            {rows.map((r) => {
              if (r.kind === 'link') {
                return <ThreadLinkCard key={r.item.id} thread={byId[r.item.threadId]} onOpen={() => setThreadId(r.item.threadId)} />
              }
              if (r.kind === 'tool') return <ToolCallCard key={r.item.id} toolCall={r.item} />
              if (r.kind === 'group') return <ToolCallGroup key={`g-${r.items[0]!.id}`} toolCalls={r.items} />
              const m = r.item
              const seed = m.turnKind === 'seed'
              return (
                <div key={m.id} className={seed ? 'rounded-xl border border-dashed border-border p-2' : ''}>
                  {seed && <p className="mb-1 pl-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">From your coach log</p>}
                  <CoachHeader label={m.label} ts={m.ts} />
                  <MessageBubble message={m} showLabel={r.showLabel} />
                  {isCoach && m.turnKind === 'proactive' && m.turnId && thread && (
                    <DiscussButton busy={busy} onClick={() => void newThread({ thread_id: thread.id, turn_id: m.turnId! })} />
                  )}
                </div>
              )
            })}
            {thinking && (
              <div className="flex items-center gap-2 pl-1 text-sm text-muted-foreground">
                <div className="thinking-dots flex items-center gap-[3px]"><span /><span /><span /></div>
              </div>
            )}
          </div>
        </div>
      )}

      <Composer
        disabled={status !== 'idle' || !me.chat.available || !threadId}
        streaming={status === 'streaming'}
        onStop={() => void stop()}
        onSend={(t, a) => void send(t, a)}
        placeholder={!me.chat.available ? 'Chat isn’t configured yet' : status === 'streaming' ? 'Rabbit is replying…' : 'Ask Rabbit…'}
      />

      {sheet === 'threads' && (
        <ThreadsSheet goal={goal} threads={threads} activeId={threadId} closing={closing}
          onSelect={(id) => { setThreadId(id); close() }}
          onNew={() => void newThread()}
          onDelete={async (id) => {
            try { await deleteThread(id); await refreshThreads() } catch (e) { window.alert((e as Error).message) }
          }}
          onEditGoal={() => open('goal')} onClose={close} />
      )}
      {sheet === 'goal' && (
        <GoalSheet goal={goal} athlete={me.athlete} closing={closing} onClose={close}
          onSaved={() => { onChange(); void refreshThreads() }} />
      )}
      {sheet === 'settings' && (
        <div className={`modal-overlay ${closing ? 'modal-overlay--exit' : ''}`} onClick={close}>
          <div className={`modal ${closing ? 'modal--exit' : ''}`} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="modal-title">Settings</h2>
              <button className="modal-close" aria-label="Close" onClick={close}><X className="size-4" /></button>
            </div>
            <div className="modal-body overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
              <SettingsPanels me={me} goal={goal} onEditGoal={() => open('goal')} onChange={onChange}
                onDevDone={() => void reload()} />
              {me.garmin.connected && <Button variant="primary" className="mt-4 w-full" onClick={close}>Done</Button>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
