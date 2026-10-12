/**
 * NDJSON stream client for Rabbit's chat (ported from Dolly's agent-stream.ts).
 * The session is a same-origin cookie, so there is no auth header to attach.
 */

export type StreamEvent =
  | { type: 'text-delta'; text: string }
  | { type: 'tool-call-start'; toolCallId: string; toolName: string; params: Record<string, unknown> }
  | { type: 'tool-call-result'; toolCallId: string; toolName: string; result: unknown }
  | { type: 'tool-call-error'; toolCallId: string; toolName: string; error: string }
  | { type: 'approval-request'; approvalCallId: string; toolName: string; params: Record<string, unknown>; summary: ApprovalSummary }
  | { type: 'approval-result'; approvalCallId: string; approved: boolean; reason: string | null }
  | { type: 'chart'; toolCallId: string; spec: ChartSpec }
  | { type: 'usage'; promptTokens: number; completionTokens: number; totalTokens: number }
  | { type: 'result'; response: AgentResponse }

import type { ChartSpec } from '@/components/chat/Chart'

export type ApprovalSummary = {
  title: string; detail?: string
  changes: { field: string; from: string | null; to: string }[]
}

export type AgentResponse =
  | { status: 'done'; turnId?: string; message: string; steps: number }
  | { status: 'error'; turnId?: string; error: string; steps: number; disconnected?: boolean }

const STREAM_TIMEOUT_MS = 60_000 // server heartbeats every 15s

function errorResult(error: string, disconnected = false): StreamEvent {
  return { type: 'result', response: { status: 'error', error, steps: 0, disconnected } }
}

async function* readNdjson(response: Response): AsyncGenerator<StreamEvent> {
  if (!response.body) { yield errorResult('No response body'); return }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      let timer: ReturnType<typeof setTimeout> | undefined
      const { done, value } = await Promise.race([
        reader.read(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error('STREAM_DISCONNECTED')), STREAM_TIMEOUT_MS)
        }),
      ]).finally(() => clearTimeout(timer))
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue // heartbeat
        try { yield JSON.parse(trimmed) as StreamEvent } catch { /* skip malformed line */ }
      }
    }
    if (buffer.trim()) {
      try { yield JSON.parse(buffer.trim()) as StreamEvent } catch { /* ignore */ }
    }
  } catch (err) {
    await reader.cancel().catch(() => {})
    // Phones drop streams when the app is backgrounded; the turn keeps running
    // server-side, so the caller can re-attach.
    yield errorResult('Connection lost', true)
  } finally {
    reader.releaseLock()
  }
}

/** Start a turn. A 409 means one is already running: re-attach to it instead. */
export async function* sendChat(message: string, threadId: string, attachmentIds: string[] = [],
  signal?: AbortSignal):
  AsyncGenerator<StreamEvent | { type: 'turn-id'; turnId: string }> {
  let response: Response
  try {
    response = await fetch('/app/api/chat', {
      method: 'POST', credentials: 'same-origin', signal,
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ message, thread_id: threadId, attachments: attachmentIds }),
    })
  } catch {
    yield errorResult("Couldn't reach Rabbit. Check your connection.", false)
    return
  }
  if (response.status === 409) {
    const body = await response.json().catch(() => ({}))
    if (body.turn_id) { yield { type: 'turn-id', turnId: body.turn_id }; yield* attachTurn(body.turn_id, 0, signal) }
    return
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    yield errorResult(body.error ?? `HTTP ${response.status}`)
    return
  }
  const turnId = response.headers.get('x-turn-id')
  if (turnId) yield { type: 'turn-id', turnId }
  yield* readNdjson(response)
}

/** Follow a running (or recently finished) turn from event index ``from``. */
export async function* attachTurn(turnId: string, from: number, signal?: AbortSignal):
  AsyncGenerator<StreamEvent> {
  let response: Response
  try {
    response = await fetch(`/app/api/chat/${encodeURIComponent(turnId)}/events?from=${from}`,
      { credentials: 'same-origin', signal })
  } catch {
    yield errorResult('Connection lost', true)
    return
  }
  if (response.status === 404) return // finished and expired: history has it
  if (!response.ok) { yield errorResult(`HTTP ${response.status}`); return }
  yield* readNdjson(response)
}
