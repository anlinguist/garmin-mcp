// Same-origin JSON API. The session is an HttpOnly cookie, so nothing auth-related
// lives in JS. Mutating calls always send JSON (the server rejects anything else).

export class AuthError extends Error {}

export type CoachSettings = {
  timezone: string
  timezone_set: boolean
  brief_time: string
  brief_enabled: boolean
  workout_feedback: boolean
  has_notes: boolean
}

export async function putSettings(patch: Partial<CoachSettings>): Promise<CoachSettings> {
  const r = await fetch('/app/api/settings', {
    method: 'PUT', credentials: 'same-origin',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify(patch),
  })
  if (r.status === 401) throw new AuthError('not signed in')
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`)
  return data as CoachSettings
}

export type Me = {
  user_id: string
  profile: { name: string; picture: string }
  chat: { available: boolean; active_turn: string | null }
  settings: CoachSettings
  athlete: Athlete
  goal: Goal | null
  dev: { fake_garmin: boolean; tools: string[] }
  garmin_connected: boolean
  garmin: {
    connected: boolean
    can_connect: boolean
    cooldown_until: number | null
    cooldown_scope: 'user' | 'global' | null
    fake: boolean
  }
  push: { available: boolean; public_key: string | null; subscriptions: number }
}

async function request(path: string, init?: RequestInit): Promise<Response> {
  const r = await fetch(`/app${path}`, { credentials: 'same-origin', ...init })
  if (r.status === 401) throw new AuthError('not signed in')
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try { msg = (await r.json()).error ?? msg } catch { /* not JSON */ }
    throw new Error(msg)
  }
  return r
}

export async function getJSON<T>(path: string): Promise<T> {
  return (await request(path)).json() as Promise<T>
}

export async function postJSON<T>(path: string, body: unknown = {}): Promise<T> {
  return (await request(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })).json() as Promise<T>
}

export const getMe = () => getJSON<Me>('/api/me')
export const signOut = () => postJSON('/auth/logout')

/** POST that returns the status and body instead of throwing on 4xx (except 401). */
export async function postRaw<T>(path: string, body: unknown = {}): Promise<{ status: number; data: T }> {
  const r = await fetch(`/app${path}`, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  })
  if (r.status === 401) throw new AuthError('not signed in')
  let data: unknown = null
  try { data = await r.json() } catch { /* empty */ }
  return { status: r.status, data: data as T }
}

// ------------------------------------------------------- coaching state ---
export type Athlete = {
  experience?: string; units?: 'metric' | 'imperial'; weekly_hours?: number | null; age?: number | null
  training_days?: string; injuries?: string; preferences?: string
}
export type Goal = {
  id: string; title: string; event: string; date: string | null; distance: string
  target: string; priorities: string; notes: string; created: number; archived_at: number | null
  plan?: string; plan_files?: { id: string; name: string; kind: string; mime: string; size: number }[]
  plan_start?: string; plan_weeks?: number
}
export type Thread = {
  id: string; kind: 'coach' | 'topic'; title: string; goal_id: string
  created: number; updated: number; seed: { thread_id: string; turn_id: string } | null
}

export async function sendJSON<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/app${path}`, {
    method, credentials: 'same-origin',
    headers: { 'content-type': 'application/json' },
    body: body === undefined ? '{}' : JSON.stringify(body),
  })
  if (r.status === 401) throw new AuthError('not signed in')
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.error ?? `HTTP ${r.status}`)
  return data as T
}

export const listThreads = () => getJSON<{ goal: Goal | null; threads: Thread[] }>('/api/threads')
export const createThread = (from?: { thread_id: string; turn_id: string }) =>
  sendJSON<Thread>('POST', '/api/threads', from ? { from } : {})
export const renameThread = (id: string, title: string) => sendJSON<Thread>('PATCH', `/api/threads/${id}`, { title })
export const deleteThread = (id: string) => sendJSON<{ ok: boolean }>('DELETE', `/api/threads/${id}`)
export const createGoal = (g: Partial<Goal>) => sendJSON<Goal>('POST', '/api/goals', g)
export const updateGoal = (id: string, g: Partial<Goal>) => sendJSON<Goal>('PATCH', `/api/goals/${id}`, g)
export const archiveGoal = (id: string) => sendJSON<{ ok: boolean }>('POST', `/api/goals/${id}/archive`)
export const putAthlete = (a: Athlete) => sendJSON<Athlete>('PUT', '/api/athlete', a)

/** "36 wks · base" style summary for headers. */
export function goalCountdown(goal: Goal | null): string {
  if (!goal?.date) return ''
  const days = Math.floor((new Date(`${goal.date}T12:00:00`).getTime() - Date.now()) / 86_400_000)
  if (days < 0) return 'done'
  if (days <= 7) return 'race week'
  const weeks = Math.floor(days / 7)
  const phase = weeks < 3 ? 'taper' : weeks < 8 ? 'peak' : weeks < 16 ? 'build' : 'base'
  return `${weeks} wks · ${phase}`
}

// "This week" in the coach log: the plan's sessions vs. what Garmin recorded.
export type Sport = 'swim' | 'bike' | 'run' | 'brick' | 'strength' | 'rest' | 'other'
export type DoneActivity = { id: number | null; name: string; sport: Sport; minutes: number; distance_km?: number | null }
export type PlannedSession = {
  sport: Sport; title: string; minutes: number | null; detail: string
  status: 'done' | 'missed' | 'planned' | 'rest'; activity?: DoneActivity
  /** set when the day was changed from the plan: the reason, and what the plan had */
  changed?: string; was?: string
}
export type WeekDay = { date: string; weekday: string; today: boolean; sessions: PlannedSession[]; extra: DoneActivity[] }
export type PlanWeek = {
  start: string; end: string; today: string
  index: number | null; weeks: number | null; weeks_to_race: number | null; phase: string | null
  plan_status: 'none' | 'ready' | 'error' | 'over_budget'; focus: string | null
  garmin_error: string | null; days: WeekDay[]
  totals: { planned_sessions: number; done_sessions: number; planned_minutes: number; done_minutes: number; extra_sessions: number }
}
export const getPlanWeek = () => getJSON<PlanWeek>('/api/plan/week')
export const resetPlanDay = (date: string) => postJSON<PlanWeek>('/api/plan/day/reset', { date })
