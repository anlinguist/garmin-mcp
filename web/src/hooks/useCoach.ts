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

export type ChatMessage = { kind: 'message'; id: string; role: 'user' | 'agent'; text: string; error?: boolean }
export type ToolCall = {
  kind: 'tool'; id: string; toolName: string; params: Record<string, unknown>
  status: 'executing' | 'done' | 'error'; result?: unknown; error?: string
}
export type ChatItem = ChatMessage | ToolCall
export type Status = 'loading' | 'idle' | 'streaming'

type Part =
  | { type: 'text'; text: string }
  | { type: 'tool_call'; toolCallId: string; toolName: string; params: Record<string, unknown>; result?: unknown; error?: string }
type StoredTurn = { id: string; ts: number; kind: 'chat' | 'proactive'; user: string | null; parts: Part[] }

let seq = 0
const uid = (p: string) => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`

function turnsToItems(turns: StoredTurn[]): ChatItem[] {
  const items: ChatItem[] = []
  for (const t of turns) {
    if (t.user) items.push({ kind: 'message', id: `${t.id}-u`, role: 'user', text: t.user })
    t.parts.forEach((p, i) => {
      if (p.type === 'text') {
        items.push({ kind: 'message', id: `${t.id}-${i}`, role: 'agent', text: p.text })
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

export function useCoach(activeTurn: string | null) {
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
    try {
      const page = await getJSON<{ turns: StoredTurn[] }>('/api/messages?limit=60')
      setItems(turnsToItems(page.turns))
    } catch {
      setItems([])
    }
  }, [])

  useEffect(() => {
    void (async () => {
      await loadHistory()
      setStatus('idle')
      if (activeTurn) { // a reply was still running when the app was closed
        turnRef.current = { id: activeTurn, seen: 0 }
        await reattach()
      }
    })()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Coming back to the app: pick up a reply that kept running in the background.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && turnRef.current && !busy.current) void reattach()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [reattach])

  const send = useCallback(async (text: string) => {
    const message = text.trim()
    if (!message || busy.current) return
    busy.current = true
    openMsg.current = null
    setItems((prev) => [...prev, { kind: 'message', id: uid('u'), role: 'user', text: message }])
    setStatus('streaming')
    let dropped = false
    try {
      dropped = await consume(sendChat(message))
    } finally {
      busy.current = false
      if (dropped && document.visibilityState === 'visible') void reattach()
      else if (!dropped) { turnRef.current = null; setStatus('idle') }
    }
  }, [consume, reattach])

  const clear = useCallback(() => setItems([]), [])

  return { items, status, send, clear, reload: loadHistory }
}
