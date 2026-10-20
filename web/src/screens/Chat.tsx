import { useSheetDrag } from '@/hooks/useSheetDrag'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, MessagesSquare, Target, X } from 'lucide-react'
import { useEdgeSwipe } from '@/hooks/useEdgeSwipe'
import { Avatar } from '@/components/Avatar'
import { MemorySheet } from '@/components/MemorySheet'
import { PlanSheet } from '@/components/PlanSheet'
import { PlanPins } from '@/components/chat/PlanPins'
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
  createThread, deleteThread, getPlanWeek, goalCountdown, listThreads, putSettings, resetPlanDay,
  type Goal, type Me, type PlannedSession, type PlanWeek, type Thread, type WeekDay,
} from '@/lib/api'
import { useCoach, type QueuedSend, type ChartItem, type Approval, type ChatItem, type ChatMessage, type ThreadLink, type ToolCall } from '@/hooks/useCoach'
import { ApprovalCard } from '@/components/chat/ApprovalCard'
import { Chart } from '@/components/chat/Chart'
import { MessageMenu, type MenuTarget } from '@/components/chat/MessageMenu'
import { ThreadPeek } from '@/components/chat/ThreadPeek'
import { EmptyHero } from '@/components/chat/EmptyHero'
import { useLongPress } from '@/hooks/useLongPress'
import { hapticTap } from '@/lib/haptics'

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

type Sheet = 'settings' | 'threads' | 'memory' | 'plan'

export function Chat({ me, onChange }: { me: Me; onChange: () => void }) {
  const [threads, setThreads] = useState<Thread[]>([])
  const [goal, setGoal] = useState<Goal | null>(me.goal)
  const [threadId, setThreadId] = useState<string | null>(null)
  const [queued, setQueued] = useState<QueuedSend | null>(null)
  // A new chat is a client-side draft until its first message is sent (then it's created).
  const [pending, setPending] = useState<{ seed?: { thread_id: string; turn_id: string }; seedMsg?: ChatMessage } | null>(null)
  const { items, ready, status, send, stop, decide, reload } =
    useCoach(threadId, me.chat.active_turn, queued, () => setQueued(null))
  const [sheet, setSheet] = useState<Sheet | null>(me.garmin.connected ? null : 'settings')
  const [closing, setClosing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [draft, setDraft] = useState<{ text: string; n: number } | null>(null)
  const [menu, setMenu] = useState<MenuTarget | null>(null)
  const [peek, setPeek] = useState<string | null>(null)
  const [toast, setToast] = useState<{ msg: string; n: number } | null>(null)
  const showToast = (msg: string) => setToast((t) => ({ msg, n: (t?.n ?? 0) + 1 }))
  const feed = useRef<HTMLDivElement>(null)
  // Was the user at the bottom of the feed? Kept current by scroll events, so that when the
  // feed resizes (keyboard opening/closing) we can keep the newest messages in view.
  const atBottom = useRef(true)

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
    setThreadId((cur) => select ?? (cur && r.threads.some((t) => t.id === cur) ? cur
      : r.threads.find((t) => t.kind === 'coach')?.id ?? r.threads[0]?.id ?? null))
  }, [])

  useEffect(() => { void refreshThreads() }, [refreshThreads])
  // After each reply: pick up a new thread title, or a goal/plan Rabbit just saved.
  const prevStatus = useRef(status)
  useEffect(() => {
    if (prevStatus.current === 'streaming' && status === 'idle') { void refreshThreads(); onChange() }
    prevStatus.current = status
  }, [status]) // eslint-disable-line react-hooks/exhaustive-deps

  // Follow the phone's time zone (first run, and after travel) so "today", the plan week
  // and the morning brief match where you are. Checked on open and on return to the app.
  const savedTz = me.settings.timezone_set ? me.settings.timezone : null
  const tzSent = useRef<string | null>(null)
  useEffect(() => {
    const check = () => {
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
      if (!tz || tz === savedTz || tz === tzSent.current) return
      tzSent.current = tz
      void putSettings({ timezone: tz }).then(() => {
        if (savedTz) showToast(`Time zone updated to ${tz.split('/').pop()!.replace(/_/g, ' ')}`)
        onChange()
      }).catch(() => { tzSent.current = null })
    }
    check()
    const onVis = () => { if (document.visibilityState === 'visible') check() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [savedTz]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keyboard opens/closes (or anything else resizes the feed): stay pinned to the newest
  // messages if that's where the user was.
  useEffect(() => {
    atBottom.current = true // a freshly opened chat starts at its newest messages
    const el = feed.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => {
      if (atBottom.current) el.scrollTop = el.scrollHeight
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [threadId, pending, items.length === 0])

  // A chat or the log opens at its newest messages, placed before paint (going back to
  // the log shows its kept rows at once, then again when its history has reloaded).
  useLayoutEffect(() => {
    const el = feed.current
    if (el) { el.scrollTop = el.scrollHeight; atBottom.current = true }
  }, [threadId, pending, ready])

  // An approval is waiting on you: bring it into view.
  const pendingCount = items.filter((it) => it.kind === 'approval' && it.status === 'pending').length
  useEffect(() => {
    if (pendingCount) feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: 'smooth' })
  }, [pendingCount])

  // Stick to the bottom while streaming, unless the user scrolled up to read. Long jumps
  // are instant: iOS can leave a smooth-scrolled feed unpainted (blank) until touched.
  useEffect(() => {
    const el = feed.current
    if (!el) return
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight
    if (gap < 150 || status !== 'streaming') {
      el.scrollTo({ top: el.scrollHeight, behavior: status === 'streaming' || gap > el.clientHeight ? 'instant' : 'smooth' })
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

  // Tapping a brief or workout-note notification opens the coach log: from a cold start
  // via ?open=coach, or (app already running) via a message from the service worker.
  const openCoach = useRef(new URLSearchParams(window.location.search).get('open') === 'coach')
  useEffect(() => {
    if (openCoach.current) window.history.replaceState(null, '', '/app/')
    const onMsg = (e: MessageEvent) => {
      if (e.data?.type !== 'open' || !String(e.data.url).includes('open=coach')) return
      openCoach.current = true
      void refreshThreads() // also picks up the new message's thread state
    }
    navigator.serviceWorker?.addEventListener('message', onMsg)
    return () => navigator.serviceWorker?.removeEventListener('message', onMsg)
  }, [refreshThreads])
  useEffect(() => {
    if (!openCoach.current || !coachId) return
    openCoach.current = false
    setPending(null)
    setThreadId(coachId)
    setSheet(null)
    setClosing(false)
  }, [coachId, threads])

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
  // The coach log is home; other chats sit on top of it, with a back button and an edge
  // swipe. The log's last rows are kept so it can be drawn underneath a chat during the
  // swipe, and shown at once on the way back while its history reloads.
  const coachRows = useRef<Row[] | null>(null)
  if (isCoach && ready && status === 'idle') coachRows.current = rows
  const shownRows = ready ? rows : isCoach ? coachRows.current ?? [] : []
  const settled = ready && status !== 'loading'
  const goBack = () => { if (coachId) openThread(coachId) }
  const inChat = !isCoach && !!coachId
  const screen = useRef<HTMLDivElement>(null)
  const under = useRef<HTMLDivElement>(null)
  const underFeed = useRef<HTMLDivElement>(null)
  const { slideIn, slideBack } = useEdgeSwipe(screen, under, { enabled: inChat && !sheet && !menu && !peek, onBack: goBack })
  // Opening a chat from the log slides it in over the log.
  const wasInChat = useRef(inChat)
  useLayoutEffect(() => {
    if (inChat && !wasInChat.current) slideIn()
    wasInChat.current = inChat
  }, [inChat, slideIn])
  // The log underneath sits at its newest entries, where the real log opens.
  useLayoutEffect(() => {
    const el = underFeed.current
    if (el) el.scrollTop = el.scrollHeight
  }, [inChat, threadId])

  // Pinned plan + "This week" cards in the coach log. Refreshed when the log opens, after
  // each reply (a plan may have changed) and when the app comes back to the foreground.
  const [week, setWeek] = useState<PlanWeek | null>(null)
  const [weekLoading, setWeekLoading] = useState(false)
  const showPins = isCoach && !!goal
  const loadWeek = useCallback(() => {
    setWeekLoading(true)
    getPlanWeek().then(setWeek, () => {}).finally(() => setWeekLoading(false))
  }, [])
  useEffect(() => {
    if (!showPins || status !== 'idle') return
    loadWeek()
    const onVis = () => { if (document.visibilityState === 'visible') loadWeek() }
    document.addEventListener('visibilitychange', onVis)
    return () => document.removeEventListener('visibilitychange', onVis)
  }, [showPins, status, goal?.plan, loadWeek])
  const askDay = (d: WeekDay, session?: PlannedSession) => {
    const when = d.today ? "today's" : `${new Date(`${d.date}T12:00:00`).toLocaleDateString([], { weekday: 'long' })}'s`
    tellRabbit(session ? `About ${when} ${session.title}: ` : `About ${d.today ? 'today' : when.slice(0, -2)}: `)
  }


  const press = useLongPress((el) => {
    if (el.dataset.threadId) { hapticTap(); setPeek(el.dataset.threadId); return }
    const id = el.dataset.msgId
    const item = items.find((it): it is ChatMessage => it.kind === 'message' && it.id === id)
    if (!item || item.error) return
    hapticTap()
    setMenu({ item, el, canDiscuss: isCoach && item.turnKind === 'proactive' && !!item.turnId })
  })

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
  const coachThread = coachId ? byId[coachId] : undefined

  function renderHeader(isLog: boolean, under = false) {
    const pinned = isLog && !!goal // the week card under it separates header and feed
    return (
      <header className={`flex shrink-0 items-center gap-2 px-3 pb-2.5 pt-[max(10px,env(safe-area-inset-top))] ${pinned ? '' : 'border-b border-(--border)'}`}>
        {!isLog && (
          <Button variant="ghost" size="icon" className="-mr-1" aria-label="Back to the coach log" onClick={slideBack}>
            <ChevronLeft className="size-5" />
          </Button>
        )}
        <button className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
          aria-label={isLog ? 'Coach log' : 'Back to the coach log'} onClick={isLog ? goBack : slideBack}>
          {/* App-icon tile, like the Home Screen icon (hops while Rabbit is replying) */}
          <span className="logo-tile flex size-[38px] shrink-0 items-center justify-center rounded-[11px] bg-(--brand) shadow-sm">
            <RabbitLogo size={32} thinking={!under && status === 'streaming'} />
          </span>
          <span className="min-w-0">
            <span className="block truncate font-display text-lg leading-tight text-(--brand)">
              {isLog ? 'Rabbit' : pending ? 'New chat' : thread?.title || 'New chat'}
            </span>
            <span className="block truncate text-[0.6471rem] leading-tight text-muted-foreground">
              {goal ? `${goal.title}${countdown ? ` · ${countdown}` : ''}` : 'No goal set'}
            </span>
          </span>
        </button>
        <Button variant="ghost" size="icon" aria-label="Chats" onClick={() => open('threads')}>
          <MessagesSquare className="size-5" />
        </Button>
        <button className="rounded-full" aria-label="Settings" onClick={() => open('settings')}>
          <Avatar name={me.profile.name || me.user_id} picture={me.profile.picture} />
        </button>
      </header>
    )
  }

  function renderRows(list: Row[], inLog: boolean) {
    return list.map((r) => {
      if (r.kind === 'link') {
        if (!byId[r.item.threadId]) return null // chat was deleted
        return <ThreadLinkCard key={r.item.id} thread={byId[r.item.threadId]} pressProps={press}
          onOpen={() => openThread(r.item.threadId)} />
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
          {seed && <p className="mb-1 pl-1 text-[0.5882rem] font-semibold uppercase tracking-widest text-muted-foreground">From your coach log</p>}
          <CoachHeader label={m.label} ts={m.ts} />
          <MessageBubble message={m} showLabel={r.showLabel} pressProps={press} />
          {inLog && m.turnKind === 'proactive' && m.turnId && coachThread && (
            <DiscussButton busy={busy} onClick={() => newThread({ thread_id: coachThread.id, turn_id: m.turnId! }, m)} />
          )}
        </div>
      )
    })
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
      {inChat && (
        // A still copy of the coach log under the open chat, revealed by the edge swipe.
        <div ref={under} inert aria-hidden className="absolute inset-0 flex flex-col bg-background" style={{ visibility: 'hidden' }}>
          {renderHeader(true, true)}
          {goal && <PlanPins goal={goal} week={week} loading={false} onOpenPlan={() => {}} onAskDay={() => {}} />}
          <div ref={underFeed} className="min-h-0 flex-1 overflow-hidden px-4">
            <div className="mx-auto max-w-2xl space-y-4 py-4">{renderRows(coachRows.current ?? [], true)}</div>
          </div>
          <Composer still disabled streaming={false} onStop={() => {}} onSend={() => {}} placeholder="Start a new chat…" />
        </div>
      )}
      <div ref={screen} className="relative flex min-h-0 flex-1 flex-col bg-background">
      {renderHeader(!pending && isCoach)}

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

      {showPins && goal && (
        <PlanPins goal={goal} week={week} loading={weekLoading}
          onOpenPlan={() => open('plan')} onAskDay={askDay}
          onResetDay={(date, offset) => resetPlanDay(date, offset).then((w) => {
            if (offset === 0) setWeek(w)
            return w
          }, (e) => { showToast((e as Error).message) })} />
      )}

      {pending ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 py-4">
          <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col">
            {pending.seedMsg && (
              <div className="rounded-xl border border-dashed border-border p-2">
                <p className="mb-1 pl-1 text-[0.5882rem] font-semibold uppercase tracking-widest text-muted-foreground">From your coach log</p>
                <CoachHeader label={pending.seedMsg.label} ts={pending.seedMsg.ts} />
                <MessageBubble message={pending.seedMsg} showLabel={false} />
              </div>
            )}
            <div className="chat-empty flex flex-1 flex-col items-center justify-center py-6 text-center">
              <EmptyHero suggestions={pending.seedMsg ? undefined : TOPIC_SUGGESTIONS.map((q) => (
                    <button key={q} disabled={!me.chat.available || busy}
                      className="w-full cursor-pointer rounded-(--radius) border border-border bg-background px-3.5 py-2.5 text-left text-[0.7647rem] text-foreground/80 transition-all hover:bg-secondary disabled:opacity-50"
                      onClick={() => void sendPending(q, [])}>{q}</button>
                  ))}>
                {!pending.seedMsg && <RabbitLogo size={64} className="mb-3 opacity-40" />}
                <p className="mb-2 font-display text-xl leading-tight text-(--brand)">
                  {pending.seedMsg ? 'What would you like to ask about this?' : 'Ready when you are.'}
                </p>
              </EmptyHero>
            </div>
          </div>
        </div>
      ) : !ready && !isCoach ? (
        // Opening a chat: Rabbit hops while its messages load.
        <div className="chat-loading flex flex-1 items-center justify-center" role="status" aria-label="Loading chat">
          <RabbitLogo size={64} thinking className="text-(--brand) opacity-60" />
        </div>
      ) : items.length === 0 && settled && isCoach && !goal ? (
        <div className="flex flex-1 flex-col justify-end overflow-y-auto px-4 py-4">
          <div className="mx-auto w-full max-w-2xl">
            <div className="mb-3 flex items-center gap-2 pl-1"><RabbitLogo size={22} />
              <span className="text-[0.6471rem] font-semibold uppercase tracking-widest text-muted-foreground">Rabbit</span></div>
            <div className="max-w-[85%] rounded-xl rounded-tl-sm border border-border bg-background px-3.5 py-2.5 text-[0.8235rem] leading-relaxed shadow-sm">
              <p>Hi{me.profile.name ? ` ${me.profile.name}` : ''}! I'm Rabbit, your training pacer.</p>
              <p className="mt-1.5">What are you training for? Tell me the race and when it is. If you have a plan from a coach or an app, send it as a PDF or screenshots and I'll keep it in mind for every brief and workout note.</p>
            </div>
            <div className="mt-3 flex flex-wrap gap-2 pl-1">
              <button className="rounded-full border border-border bg-background px-3 py-1.5 text-[0.7647rem] hover:bg-secondary"
                onClick={() => tellRabbit("I'm training for ")}>I'm training for…</button>
              <label htmlFor={ATTACH_INPUT_ID} onClick={() => setDraft((d) => ({ text: "Here's my training plan.", n: (d?.n ?? 0) + 1 }))}
                className="cursor-pointer rounded-full border border-border bg-background px-3 py-1.5 text-[0.7647rem] hover:bg-secondary">Upload my plan</label>
            </div>
          </div>
        </div>
      ) : items.length === 0 && settled ? (
        <div className="chat-empty flex flex-1 flex-col items-center justify-center px-6 text-center">
          <EmptyHero gap={20} suggestions={suggestions.map((s) => (
              <button key={s} disabled={!me.chat.available}
                className="w-full cursor-pointer rounded-(--radius) border border-border bg-background px-3.5 py-2.5 text-left text-[0.7647rem] text-foreground/80 transition-all hover:bg-secondary disabled:opacity-50"
                onClick={() => void (isCoach ? sendFromLog(s, []) : send(s))}>{s}</button>
            ))}>
          <RabbitLogo size={80} className="mb-3 opacity-40" />
          <p className="mb-2 font-display text-2xl leading-tight text-(--brand)">
            {isCoach ? 'Your coach log' : 'Ready when you are.'}
          </p>
          <p className="text-[0.7647rem] text-muted-foreground">
            {isCoach ? 'Morning briefs and workout notes land here. Ask anything about your training.'
              : 'Ask about your training, recovery or what to do next.'}
          </p>
          </EmptyHero>
        </div>
      ) : (
        <div ref={feed} className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4"
          onScroll={(e) => {
            const el = e.currentTarget
            atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
          }}>
          <div className="mx-auto max-w-2xl space-y-4 py-4">
            {renderRows(shownRows, isCoach)}
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
      </div>

      {menu && (
        <MessageMenu target={menu} onClose={() => setMenu(null)} onToast={showToast}
          onDiscuss={() => { if (thread && menu.item.turnId) newThread({ thread_id: thread.id, turn_id: menu.item.turnId }, menu.item) }} />
      )}
      {peek && byId[peek] && (
        <ThreadPeek thread={byId[peek]!} onClose={() => setPeek(null)}
          onOpen={() => { setPeek(null); openThread(peek) }}
          onDelete={async () => {
            try {
              await deleteThread(peek)
              setPeek(null)
              await refreshThreads()
              reload()
              showToast('Chat deleted')
            } catch (e) { window.alert((e as Error).message) }
          }} />
      )}
      {toast && <div key={toast.n} className="rabbit-toast" role="status">{toast.msg}</div>}

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
      {sheet === 'plan' && goal && (
        <PlanSheet goal={goal} week={week} closing={closing} onClose={close} onDismissed={dismissNow}
          onAsk={() => tellRabbit('About my plan: ')}
          onUploadPlan={() => tellRabbit("Here's my updated training plan.")}
          onEdit={() => open('memory')} />
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
