// Same-origin JSON API. The session is an HttpOnly cookie, so nothing auth-related
// lives in JS. Mutating calls always send JSON (the server rejects anything else).

export class AuthError extends Error {}

export type Me = {
  user_id: string
  profile: { name: string; picture: string }
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

export async function postStream(path: string, body: unknown = {}): Promise<Response> {
  return request(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
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
