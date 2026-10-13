/**
 * useCoach: chat state for Rabbit's single ongoing thread.
 *
 * A slimmed-down port of Dolly's useAgent: keeps the processStream switch
 * (text deltas append to the open agent message; a tool call closes it so
 * cards land in order) and history loading. Turns run server-side, so when a
 * phone drops the stream we re-attach from the last event index.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { getJSON } from '@/lib/api'
import { attachTurn, sendChat, type ApprovalSummary, type StreamEvent } from '@/lib/agent-stream'
import type { AttachmentMeta } from '@/lib/attachments'
import type { ChartSpec } from '@/components/chat/Chart'

export type ChatMessage = {
  kind: 'message'; id: string; role: 'user' | 'agent'; text: string; error?: boolean
  /** coach-log metadata: which stored turn this came from, and what kind */
  turnId?: string; ts?: number; label?: 'brief' | 'workout' | 'note' | null; turnKind?: string
  attachments?: AttachmentMeta[]
}
export type ThreadLink = { kind: 'link'; id: string; threadId: string; fromTurn: string | null; ts: number }
export type ToolCall = {
  kind: 'tool'; id: string; toolName: string; params: Record<string, unknown>
  status: 'executing' | 'done' | 'error'; result?: unknown; error?: string
}
export type Approval = {
  kind: 'approval'; id: string; turnId: string | null; summary: ApprovalSummary
  status: 'pending' | 'approved' | 'declined'; reason?: string | null
}
export type ChartItem = { kind: 'chart'; id: string; spec: ChartSpec }
export type ChatItem = ChatMessage | ToolCall | ThreadLink | Approval | ChartItem
export type Status = 'loading' | 'idle' | 'streaming'

type Part =
  | { type: 'text'; text: string }
  | { type: 'thread_link'; thread_id: string; from_turn: string | null }
  | { type: 'chart'; spec: ChartSpec }
  | { type: 'tool_call'; toolCallId: string; toolName: string; params: Record<string, unknown>; result?: unknown; error?: string
      approval?: { summary: ApprovalSummary; approved: boolean; reason: string | null } }
type StoredTurn = {
  id: string; ts: number; kind: 'chat' | 'proactive' | 'seed' | 'link'
  label?: 'brief' | 'workout' | null; user: string | null; parts: Part[]
  attachments?: AttachmentMeta[]
}

let seq = 0
const uid = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`

function turnsToItems(turns: StoredTurn[]): ChatItem[] {
  const items: ChatItem[] = []
  for (const t of turns) {
    if (t.user) {
      const text = t.attachments?.length && t.user === '(see attached)' ? '' : t.user
      items.push({ kind: 'message', id: `${t.id}-u`, role: 'user', text, ts: t.ts, attachments: t.attachments })
    }
    let first = true
    t.parts.forEach((p, i) => {
      if (p.type === 'chart') {
        items.push({ kind: 'chart', id: `${t.id}-${i}`, spec: p.spec })
      } else if (p.type === 'thread_link') {
        items.push({ kind: 'link', id: `${t.id}-${i}`, threadId: p.thread_id, fromTurn: p.from_turn, ts: t.ts })
      } else if (p.type === 'text') {
        items.push({ kind: 'message', id: `${t.id}-${i}`, role: 'agent', text: p.text, ts: t.ts,
          turnId: t.id, turnKind: t.kind, label: first ? (t.label ?? (t.kind === 'proactive' ? 'note' : null)) : null })
        first = false
      } else {
        if (p.approval) {
          items.push({ kind: 'approval', id: `ap-${p.toolCallId}`, turnId: t.id, summary: p.approval.summary,
            status: p.approval.approved ? 'approved' : 'declined', reason: p.approval.reason })
        }
        items.push({
          kind: 'tool', id: p.toolCallId, toolName: p.toolName, params: p.params ?? {},
          status: p.error ? 'error' : 'done', result: p.result, error: p.error,
        })
      }
    })
  }
  return items
}

export type QueuedSend = { threadId: string; text: string; attachments: AttachmentMeta[] }

/** ``queued``: a message to send as soon as ``queued.threadId`` is open (used when a
 *  message typed in the coach log becomes a new thread). */
export function useCoach(threadId: string | null, activeTurn: string | null,
  queued?: QueuedSend | null, onQueuedSent?: () => void) {
  const queuedRef = useRef(queued)
  queuedRef.current = queued
  const [items, setItems] = useState<ChatItem[]>([])
  const [status, setStatus] = useState<Status>('loading')
  // Which thread ``items`` belong to: right after a switch they're still the old thread's.
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const turnRef = useRef<{ id: string; seen: number } | null>(null)
  const openMsg = useRef<string | null>(null)
  const busy = useRef(false)

  const apply = useCallback((e: StreamEvent) => {
    switch (e.type) {
      case 'text-delta': {
        const id = openMsg.current
        if (id) {
          setItems((prev) => prev.map((it) => (it.id === id && it.kind === 'message'
            ? { ...it, text: it.text + e.text } : it)))
        } else {
          const nid = uid('a')
          openMsg.current = nid
          setItems((prev) => [...prev, { kind: 'message', id: nid, role: 'agent', text: e.text }])
        }
        break
      }
      case 'tool-call-start':
        openMsg.current = null
        setItems((prev) => [...prev, { kind: 'tool', id: e.toolCallId, toolName: e.toolName,
          params: e.params ?? {}, status: 'executing' }])
        break
      case 'tool-call-result':
        setItems((prev) => prev.map((it) => (it.id === e.toolCallId && it.kind === 'tool'
          ? { ...it, status: 'done', result: e.result } : it)))
        break
      case 'tool-call-error':
        setItems((prev) => prev.map((it) => (it.id === e.toolCallId && it.kind === 'tool'
          ? { ...it, status: 'error', error: e.error } : it)))
        break
      case 'chart':
        openMsg.current = null
        // The chart replaces the tool card that announced it.
        setItems((prev) => [...prev.filter((it) => it.id !== e.toolCallId),
          { kind: 'chart', id: `c-${e.toolCallId}`, spec: e.spec }])
        break
      case 'approval-request':
        openMsg.current = null
        setItems((prev) => [...prev, { kind: 'approval', id: `ap-${e.approvalCallId}`,
          turnId: turnRef.current?.id ?? null, summary: e.summary, status: 'pending' }])
        break
      case 'approval-result':
        setItems((prev) => prev.map((it) => (it.id === `ap-${e.approvalCallId}` && it.kind === 'approval'
          ? { ...it, status: e.approved ? 'approved' : 'declined', reason: e.reason } : it)))
        break
      case 'result':
        openMsg.current = null
        if (e.response.status === 'error' && !e.response.disconnected) {
          setItems((prev) => [...prev, { kind: 'message', id: uid('e'), role: 'agent',
            text: e.response.status === 'error' ? e.response.error : '', error: true }])
        }
        break
      default:
        break
    }
  }, [])

  /** Consume a stream; returns true if it ended because the connection dropped. */
  const consume = useCallback(async (stream: AsyncGenerator<StreamEvent | { type: 'turn-id'; turnId: string }>) => {
    for await (const e of stream) {
      if (e.type === 'turn-id') { turnRef.current = { id: e.turnId, seen: 0 }; continue }
      if (e.type === 'result' && e.response.status === 'error' && e.response.disconnected) return true
      if (turnRef.current) turnRef.current.seen += 1
      apply(e)
    }
    return false
  }, [apply])

  const reattach = useCallback(async () => {
    const t = turnRef.current
    if (!t || busy.current) return
    busy.current = true
    setStatus('streaming')
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        const dropped = await consume(attachTurn(t.id, t.seen))
        if (!dropped) break
        await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)))
      }
    } finally {
      busy.current = false
      turnRef.current = null
      setStatus('idle')
    }
  }, [consume])

  const loadHistory = useCallback(async () => {
    if (!threadId) return
    try {
      const page = await getJSON<{ turns: StoredTurn[] }>(`/api/threads/${threadId}/messages?limit=60`)
      setItems(turnsToItems(page.turns))
    } catch {
      setItems([])
    }
    setLoadedId(threadId)
  }, [threadId])

  const first = useRef(true)
  useEffect(() => {
    if (!threadId) return
    openMsg.current = null
    setStatus('loading')
    void (async () => {
      await loadHistory()
      setStatus('idle')
      if (first.current && activeTurn) { // a reply was still running when the app was closed
        turnRef.current = { id: activeTurn, seen: 0 }
        await reattach()
      }
      first.current = false
      const q = queuedRef.current
      if (q && q.threadId === threadId) {
        onQueuedSent?.()
        await send(q.text, q.attachments)
      }
    })()
  }, [threadId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Coming back to the app: pick up a reply that kept running in the background.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible' || busy.current) return
      if (turnRef.current) void reattach()
      else void loadHistory() // pick up workout notes / briefs written while away
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [reattach, loadHistory])

  const send = useCallback(async (text: string, attachments: AttachmentMeta[] = []) => {
    const message = text.trim()
    if ((!message && !attachments.length) || busy.current || !threadId) return
    busy.current = true
    openMsg.current = null
    setItems((prev) => [...prev, { kind: 'message', id: uid('u'), role: 'user', text: message, attachments }])
    setStatus('streaming')
    let dropped = false
    try {
      dropped = await consume(sendChat(message, threadId, attachments.map((a) => a.id)))
    } finally {
      busy.current = false
      if (dropped && document.visibilityState === 'visible') void reattach()
      else if (!dropped) { turnRef.current = null; setStatus('idle') }
    }
  }, [consume, reattach, threadId])

  const clear = useCallback(() => setItems([]), [])

  const stop = useCallback(async () => {
    const t = turnRef.current
    if (!t) return
    await fetch(`/app/api/chat/${encodeURIComponent(t.id)}/cancel`, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'content-type': 'application/json' }, body: '{}',
    }).catch(() => {})
  }, [])

  const decide = useCallback(async (approvalId: string, approved: boolean, reason?: string) => {
    const t = turnRef.current
    if (!t) return
    const callId = approvalId.replace(/^ap-/, '')
    await fetch(`/app/api/chat/${encodeURIComponent(t.id)}/approval`, {
      method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ approval_id: callId, approved, reason: reason || null }),
    }).catch(() => {})
  }, [])

  return { items, ready: loadedId === threadId, status, send, stop, decide, clear, reload: loadHistory }
}
