import { useSheetDrag } from '@/hooks/useSheetDrag'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Menu, MessageSquarePlus, Target, X } from 'lucide-react'
import { Avatar } from '@/components/Avatar'
import { MemorySheet } from '@/components/MemorySheet'
import { RabbitLogo } from '@/components/RabbitLogo'
import { SettingsPanels } from '@/components/SettingsPanels'
import { ThreadsSheet } from '@/components/ThreadsSheet'
import { CoachHeader, DiscussButton, ThreadLinkCard } from '@/components/chat/CoachCards'
import { ATTACH_INPUT_ID, Composer } from '@/components/chat/Composer'
import type { AttachmentMeta } from '@/lib/attachments'
import { MessageBubble } from '@/components/chat/MessageBubble'
import { ToolCallCard, ToolCallGroup } from '@/components/chat/ToolCallCard'
import { Button } from '@/components/ui/button'
import {
  createThread, deleteThread, goalCountdown, listThreads, putSettings,
  type Goal, type Me, type Thread,
} from '@/lib/api'
import { useCoach, type QueuedSend, type ChartItem, type Approval, type ChatItem, type ChatMessage, type ThreadLink, type ToolCall } from '@/hooks/useCoach'
import { ApprovalCard } from '@/components/chat/ApprovalCard'
import { Chart } from '@/components/chat/Chart'

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
  | { kind: 'approval'; item: Approval }
  | { kind: 'chart'; item: ChartItem }

// Consecutive tool calls collapse into one group (Dolly's ToolCallGroup); a
// speaker label shows only when the speaker changes.
function toRows(items: ChatItem[], hideTools = false): Row[] {
  const rows: Row[] = []
  if (hideTools) items = items.filter((it) => it.kind !== 'tool')
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
    if (it.kind === 'approval') { rows.push({ kind: 'approval', item: it }); lastSpeaker = 'agent'; continue }
    if (it.kind === 'chart') { rows.push({ kind: 'chart', item: it }); lastSpeaker = 'agent'; continue }
    rows.push({ kind: 'message', item: it, showLabel: lastSpeaker !== it.role && !it.label })
    lastSpeaker = it.role
  }
  return rows
}

type Sheet = 'settings' | 'threads' | 'memory'

export function Chat({ me, onChange }: { me: Me; onChange: () => void }) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [goal, setGoal] = useState<Goal | null>(me.goal)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [queued, setQueued] = useState<QueuedSend | null>(null)
  // A new chat is a client-side draft until its first message is sent (then it's created).
  const [pending, setPending] = useState<{ seed?: { thread_id: string; turn_id: string }; seedMsg?: ChatMessage } | null>(null)
  const { items, status, send, stop, decide, reload } =
    useCoach(threadId, me.chat.active_turn, queued, () => setQueued(null))
  const [sheet, setSheet] = useState<Sheet | null>(me.garmin.connected ? null : 'settings')
  const [closing, setClosing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<{ text: string; n: number } | null>(null)
  const feed = useRef<HTMLDivElement>(null)

  const thread = threads.find((t) => t.id === threadId) ?? null
  const byId = useMemo(() => Object.fromEntries(threads.map((t) => [t.id, t])), [threads])
  // The coach log is a timeline: no lookup cards there (they stay in topic threads).
  const rows = useMemo(() => toRows(items, thread?.kind === 'coach'), [items, thread?.kind])
  const last = items[items.length - 1]
  const waitingOnYou = items.some((it) => it.kind === 'approval' && it.status === 'pending')
  const thinking = status === 'streaming' && !waitingOnYou && !(last?.kind === 'message' && last.role === 'agent')

  const refreshThreads = useCallback(async (select?: string) => {
    const r = await listThreads()
    setThreads(r.threads)
    setGoal(r.goal)
    setThreadId((cur) => select ?? (cur && r.threads.some((t) => t.id === cur) ? cur : r.threads[0]?.id ?? null))
  }, [])

  useEffect(() => { void refreshThreads() }, [refreshThreads])
  // After each reply: pick up a new thread title, or a goal/plan Rabbit just saved.
  const prevStatus = useRef(status)
  useEffect(() => {
    if (prevStatus.current === 'streaming' && status === 'idle') { void refreshThreads(); onChange() }
    prevStatus.current = status
  }, [status]) // eslint-disable-line react-hooks/exhaustive-deps

  // First run: record the phone's time zone so the morning brief lands at local time.
  useEffect(() => {
    if (me.settings.timezone_set) return
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (tz) void putSettings({ timezone: tz }).then(onChange).catch(() => {})
  }, [me.settings.timezone_set]) // eslint-disable-line react-hooks/exhaustive-deps

  // An approval is waiting on you: bring it into view.
  const pendingCount = items.filter((it) => it.kind === 'approval' && it.status === 'pending').length
  useEffect(() => {
    if (pendingCount) feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: 'smooth' })
  }, [pendingCount])

  // Stick to the bottom while streaming, unless the user scrolled up to read.
  useEffect(() => {
    const el = feed.current
    if (!el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 150 || status !== 'streaming') {
      el.scrollTo({ top: el.scrollHeight, behavior: status === 'streaming' ? 'instant' : 'smooth' })
    }
  }, [items, status])

  const open = (s: Sheet) => { setClosing(false); setSheet(s) }
  const coachId = threads.find((t) => t.kind === 'coach')?.id ?? null
  // "Tell Rabbit…": go to the coach log with the composer prefilled.
  const tellRabbit = (text: string) => {
    setPending(null)
    if (coachId) setThreadId(coachId)
    setDraft((d) => ({ text, n: (d?.n ?? 0) + 1 }))
    if (sheet) { setSheet(null); setClosing(false) }
  }
  const close = () => { setClosing(true); setTimeout(() => { setSheet(null); setClosing(false) }, 200) }
  // Drag-dismiss animates itself out, then unmounts at once (no second exit animation).
  const dismissNow = useCallback(() => { setSheet(null); setClosing(false) }, [])
  const settingsDrag = useSheetDrag(dismissNow)

  function newThread(from?: { thread_id: string; turn_id: string }, seedMsg?: ChatMessage) {
    setPending({ seed: from, seedMsg })
    if (sheet) close()
  }

  const openThread = (id: string) => { setPending(null); setThreadId(id) }

  // First message of a draft chat: only now is the thread created on the server.
  async function sendPending(text: string, attachments: AttachmentMeta[]) {
    if (!pending) return
    setBusy(true)
    try {
      const t = await createThread(pending.seed)
      setQueued({ threadId: t.id, text, attachments })
      setPending(null)
      await refreshThreads(t.id)
    } catch (e) {
      window.alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const isCoach = !pending && thread?.kind === 'coach'

  // The coach log stays a timeline: a message typed there starts a new topic thread.
  // A brief or workout note from the last 18 hours comes along as context (like Discuss).
  async function sendFromLog(text: string, attachments: AttachmentMeta[]) {
    if (!thread) return
    const cutoff = Date.now() / 1000 - 18 * 3600
    const recent = [...items].reverse().find((it): it is ChatMessage =>
      it.kind === 'message' && it.turnKind === 'proactive' && !!it.turnId && (it.ts ?? 0) > cutoff)
    setBusy(true)
    try {
      const t = await createThread(recent ? { thread_id: thread.id, turn_id: recent.turnId! } : undefined)
      setQueued({ threadId: t.id, text, attachments })
      await refreshThreads(t.id)
    } catch (e) {
      window.alert((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
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
              {pending ? 'New chat' : isCoach || !thread ? 'Rabbit' : thread.title || 'New chat'}
            </span>
            <span className="block truncate text-[11px] leading-tight text-muted-foreground">
              {goal ? `${goal.title}${countdown ? ` · ${countdown}` : ''}` : 'No goal set'}
              {isCoach ? ' · Coach log' : ''}
            </span>
          </span>
        </button>
        <Button variant="ghost" size="icon" aria-label="New chat" disabled={busy} onClick={() => newThread()}>
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
      {!goal && isCoach && items.length > 0 && (
        <button onClick={() => tellRabbit("I'm training for ")}
          className="mx-4 mt-3 flex items-center gap-2 rounded-(--radius) border border-(--accent)/30 bg-(--accent-subtle) px-3 py-2 text-left text-xs text-(--accent)">
          <Target className="size-4 shrink-0" />
          Tell Rabbit what you're training for so briefs and feedback fit your plan.
        </button>
      )}

      {pending ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4">
          <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col">
            {pending.seedMsg && (
              <div className="rounded-xl border border-dashed border-border p-2">
                <p className="mb-1 pl-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">From your coach log</p>
                <CoachHeader label={pending.seedMsg.label} ts={pending.seedMsg.ts} />
                <MessageBubble message={pending.seedMsg} showLabel={false} />
              </div>
            )}
            <div className="chat-empty flex flex-1 flex-col items-center justify-center py-6 text-center">
              {!pending.seedMsg && <RabbitLogo size={64} className="mb-3 opacity-40" />}
              <p className="mb-2 font-display text-xl leading-tight text-(--brand)">
                {pending.seedMsg ? 'What would you like to ask about this?' : 'Ready when you are.'}
              </p>
              {!pending.seedMsg && (
                <div className="mt-3 flex w-full max-w-[300px] flex-col gap-2">
                  {TOPIC_SUGGESTIONS.map((q) => (
                    <button key={q} disabled={!me.chat.available || busy}
                      className="w-full cursor-pointer rounded-(--radius) border border-border bg-background px-3.5 py-2.5 text-left text-[13px] text-foreground/80 transition-all hover:bg-secondary disabled:opacity-50"
                      onClick={() => void sendPending(q, [])}>{q}</button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : items.length === 0 && status !== 'loading' && isCoach && !goal ? (
        <div className="flex flex-1 flex-col justify-end overflow-y-auto px-4 py-4">
          <div className="mx-auto w-full max-w-2xl">
            <div className="mb-3 flex items-center gap-2 pl-1"><RabbitLogo size={22} />
              <span className="text-[11px] font-semibold uppercase tracking-widest text-muted-foreground">Rabbit</span></div>
            <div className="max-w-[85%] rounded-xl rounded-tl-sm border border-border bg-background px-3.5 py-2.5 text-[14px] leading-relaxed shadow-sm">
              <p>Hi{me.profile.name ? ` ${me.profile.name}` : ''}! I'm Rabbit, your training pacer.</p>
              <p className="mt-1.5">What are you training for? Tell me the race and when it is. If you have a plan from a coach or an app, send it as a PDF or screenshots and I'll keep it in mind for every brief and workout note.</p>
            </div>
            <div className="mt-3 flex flex-wrap gap-2 pl-1">
              <button className="rounded-full border border-border bg-background px-3 py-1.5 text-[13px] hover:bg-secondary"
                onClick={() => tellRabbit("I'm training for ")}>I'm training for…</button>
              <label htmlFor={ATTACH_INPUT_ID} onClick={() => setDraft((d) => ({ text: "Here's my training plan.", n: (d?.n ?? 0) + 1 }))}
                className="cursor-pointer rounded-full border border-border bg-background px-3 py-1.5 text-[13px] hover:bg-secondary">Upload my plan</label>
            </div>
          </div>
        </div>
      ) : items.length === 0 && status !== 'loading' ? (
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
                onClick={() => void (isCoach ? sendFromLog(s, []) : send(s))}>{s}</button>
            ))}
          </div>
        </div>
      ) : (
        <div ref={feed} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4">
          <div className="mx-auto max-w-2xl space-y-4 py-4">
            {rows.map((r) => {
              if (r.kind === 'link') {
                if (!byId[r.item.threadId]) return null // chat was deleted
                return <ThreadLinkCard key={r.item.id} thread={byId[r.item.threadId]} onOpen={() => openThread(r.item.threadId)} />
              }
              if (r.kind === 'chart') {
                return (
                  <div key={r.item.id} className="w-full max-w-[92%] [&>figure]:my-0 [&>figure]:bg-background">
                    <Chart spec={r.item.spec} />
                  </div>
                )
              }
              if (r.kind === 'approval') {
                return <ApprovalCard key={r.item.id} item={r.item}
                  onDecide={(ok, why) => void decide(r.item.id, ok, why).then(() => { if (ok) onChange() })} />
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
                    <DiscussButton busy={busy} onClick={() => newThread({ thread_id: thread.id, turn_id: m.turnId! }, m)} />
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
        disabled={pending ? (!me.chat.available || busy || status === 'streaming')
          : (status !== 'idle' || !me.chat.available || !threadId || busy)}
        streaming={status === 'streaming'}
        onStop={() => void stop()}
        onSend={(t, a) => void (pending ? sendPending(t, a) : isCoach ? sendFromLog(t, a) : send(t, a))}
        draft={draft}
        placeholder={!me.chat.available ? 'Chat isn’t configured yet' : waitingOnYou ? 'Approve or decline above…'
          : status === 'streaming' ? 'Rabbit is replying…' : isCoach ? 'Start a new chat…' : 'Ask Rabbit…'}
      />

      {sheet === 'threads' && (
        <ThreadsSheet goal={goal} threads={threads} activeId={threadId} closing={closing} onDismissed={dismissNow}
          onSelect={(id) => { openThread(id); close() }}
          onNew={() => newThread()}
          onDelete={async (id) => {
            try { await deleteThread(id); await refreshThreads() } catch (e) { window.alert((e as Error).message) }
          }}
          onEditGoal={() => open('memory')} onClose={close} />
      )}
      {sheet === 'memory' && (
        <MemorySheet goal={goal} athlete={me.athlete} closing={closing} onClose={close} onDismissed={dismissNow}
          onChanged={() => { onChange(); void refreshThreads() }}
          onTell={tellRabbit}
          onUploadPlan={() => tellRabbit("Here's my updated training plan.")} />
      )}
      {sheet === 'settings' && (
        <div className={`modal-overlay ${closing ? 'modal-overlay--exit' : ''}`} onClick={close}>
          <div className={`modal ${closing ? 'modal--exit' : ''}`} onClick={(e) => e.stopPropagation()} {...settingsDrag}>
            <div className="sheet-handle" />
            <div className="modal-header">
              <h2 className="modal-title">Settings</h2>
              <button className="modal-close" aria-label="Close" onClick={close}><X className="size-4" /></button>
            </div>
            <div className="modal-body overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">
              <SettingsPanels me={me} goal={goal} onEditGoal={() => open('memory')} onChange={onChange}
                onDevDone={() => void reload()} />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
