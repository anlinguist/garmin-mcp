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
import { attachTurn, sendChat, type StreamEvent } from '@/lib/agent-stream'

export type ChatMessage = {
  kind: 'message'; id: string; role: 'user' | 'agent'; text: string; error?: boolean
  /** coach-log metadata: which stored turn this came from, and what kind */
  turnId?: string; ts?: number; label?: 'brief' | 'workout' | 'note' | null; turnKind?: string
}
export type ThreadLink = { kind: 'link'; id: string; threadId: string; fromTurn: string | null; ts: number }
export type ToolCall = {
  kind: 'tool'; id: string; toolName: string; params: Record<string, unknown>
  status: 'executing' | 'done' | 'error'; result?: unknown; error?: string
}
export type ChatItem = ChatMessage | ToolCall | ThreadLink
export type Status = 'loading' | 'idle' | 'streaming'

type Part =
  | { type: 'text'; text: string }
  | { type: 'thread_link'; thread_id: string; from_turn: string | null }
  | { type: 'tool_call'; toolCallId: string; toolName: string; params: Record<string, unknown>; result?: unknown; error?: string }
type StoredTurn = {
  id: string; ts: number; kind: 'chat' | 'proactive' | 'seed' | 'link'
  label?: 'brief' | 'workout' | null; user: string | null; parts: Part[]
}

let seq = 0
const uid = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`

function turnsToItems(turns: StoredTurn[]): ChatItem[] {
  const items: ChatItem[] = []
  for (const t of turns) {
    if (t.user) items.push({ kind: 'message', id: `${t.id}-u`, role: 'user', text: t.user, ts: t.ts })
    let first = true
    t.parts.forEach((p, i) => {
      if (p.type === 'thread_link') {
        items.push({ kind: 'link', id: `${t.id}-${i}`, threadId: p.thread_id, fromTurn: p.from_turn, ts: t.ts })
      } else if (p.type === 'text') {
        items.push({ kind: 'message', id: `${t.id}-${i}`, role: 'agent', text: p.text, ts: t.ts,
          turnId: t.id, turnKind: t.kind, label: first ? (t.label ?? (t.kind === 'proactive' ? 'note' : null)) : null })
        first = false
      } else {
        items.push({
          kind: 'tool', id: p.toolCallId, toolName: p.toolName, params: p.params ?? {},
          status: p.error ? 'error' : 'done', result: p.result, error: p.error,
        })
      }
    })
  }
  return items
}

export function useCoach(threadId: string | null, activeTurn: string | null) {
  const [items, setItems] = useState<ChatItem[]>([])
  const [status, setStatus] = useState<Status>('loading')
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

  const send = useCallback(async (text: string) => {
    const message = text.trim()
    if (!message || busy.current || !threadId) return
    busy.current = true
    openMsg.current = null
    setItems((prev) => [...prev, { kind: 'message', id: uid('u'), role: 'user', text: message }])
    setStatus('streaming')
    let dropped = false
    try {
      dropped = await consume(sendChat(message, threadId))
    } finally {
      busy.current = false
      if (dropped && document.visibilityState === 'visible') void reattach()
      else if (!dropped) { turnRef.current = null; setStatus('idle') }
    }
  }, [consume, reattach, threadId])

  const clear = useCallback(() => setItems([]), [])

  return { items, status, send, clear, reload: loadHistory }
}
