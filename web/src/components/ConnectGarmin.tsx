import { useEffect, useRef, useState } from 'react'
import { Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { getJSON, postRaw } from '@/lib/api'

type Job = {
  job_id: string
  state: 'pending' | 'needs_mfa' | 'ok' | 'error' | 'cooldown'
  error: string | null
  mfa_tries_left: number | null
  cooldown_until?: number | null
  cooldown_scope?: 'user' | 'global' | null
}

const ERRORS: Record<string, string> = {
  bad_credentials: "Garmin didn't accept that email or password.",
  bad_mfa: "That code didn't work. Check the latest one from Garmin.",
  rate_limited: 'Garmin is limiting sign-ins right now.',
  blocked: "Garmin's security check blocked the sign-in.",
  failed: 'Something went wrong talking to Garmin.',
  expired: 'That sign-in timed out. Please start again.',
}

function fmtTime(epochSec: number): string {
  return new Date(epochSec * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

export function ConnectGarmin({ cooldownUntil, onConnected }: {
  cooldownUntil: number | null
  onConnected: () => void
}) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [until, setUntil] = useState<number | null>(cooldownUntil)
  const [submitting, setSubmitting] = useState(false)
  const poll = useRef<ReturnType<typeof setTimeout> | null>(null)
  const now = useNow(until !== null)
  const coolingDown = until !== null && until > now

  useEffect(() => () => { if (poll.current) clearTimeout(poll.current) }, [])

  function handle(j: Job) {
    setJob(j)
    if (j.state === 'pending') {
      poll.current = setTimeout(async () => {
        try { handle(await getJSON<Job>(`/api/garmin/login/${j.job_id}`)) }
        catch (e) { setMessage((e as Error).message); setJob(null) }
      }, 2000)
      return
    }
    if (j.state === 'ok') { setPassword(''); onConnected(); return }
    if (j.state === 'needs_mfa') { setMessage(j.error ? ERRORS[j.error] : null); return }
    // error or cooldown
    setPassword('')
    setCode('')
    const base = j.error ? ERRORS[j.error] ?? ERRORS.failed : null
    if (j.cooldown_until) {
      setUntil(j.cooldown_until)
      setMessage(j.cooldown_scope === 'global'
        ? `${base ?? ERRORS.rate_limited} To avoid a longer block, Rabbit will wait until ${fmtTime(j.cooldown_until)} before trying again.`
        : `${base ?? ''} You can try again at ${fmtTime(j.cooldown_until)}.`.trim())
    } else {
      setMessage(base)
    }
  }

  async function submitLogin(e: React.FormEvent) {
    e.preventDefault()
    setMessage(null)
    setSubmitting(true)
    try {
      const r = await postRaw<Job & { error?: string }>('/api/garmin/login', { email, password })
      if (r.status === 202 || r.status === 429) handle(r.data)
      else setMessage(r.data?.error ?? `Error ${r.status}`)
    } catch (err) {
      setMessage((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  async function submitCode(e: React.FormEvent) {
    e.preventDefault()
    if (!job) return
    setMessage(null)
    setSubmitting(true)
    try {
      const r = await postRaw<Job & { error?: string }>('/api/garmin/mfa', { job_id: job.job_id, code: code.trim() })
      if (r.status === 202) { setCode(''); handle(r.data) }
      else setMessage(r.data?.error ?? `Error ${r.status}`)
    } finally {
      setSubmitting(false)
    }
  }

  if (job?.state === 'pending') {
    return (
      <div className="flex items-center gap-2 text-sm text-(--text-secondary)">
        <span className="thinking-dots flex shrink-0 items-center gap-[3px]"><span /><span /><span /></span>
        Connecting to Garmin… this can take up to a minute.
      </div>
    )
  }

  if (job?.state === 'needs_mfa') {
    return (
      <form onSubmit={submitCode} className="flex flex-col gap-2">
        <p className="text-sm text-(--text-secondary)">
          Garmin sent you a verification code (email or text). Enter it here.
        </p>
        <Input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={8}
          placeholder="123456" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
          className="text-base tracking-widest" autoFocus />
        {message && <p className="text-xs text-(--danger)">{message}</p>}
        {job.mfa_tries_left != null && job.mfa_tries_left < 3 && (
          <p className="text-xs text-(--text-muted)">{job.mfa_tries_left} tries left</p>
        )}
        <Button type="submit" variant="primary" loading={submitting} disabled={code.length < 4}>Verify</Button>
      </form>
    )
  }

  return (
    <form onSubmit={submitLogin} className="flex flex-col gap-2">
      <Input type="email" autoComplete="username" inputMode="email" placeholder="Garmin email"
        value={email} onChange={(e) => setEmail(e.target.value)} className="text-base" required />
      <Input type="password" autoComplete="current-password" placeholder="Garmin password"
        value={password} onChange={(e) => setPassword(e.target.value)} className="text-base" required />
      {message && <p className={`text-xs ${coolingDown ? 'text-(--warning)' : 'text-(--danger)'}`}>{message}</p>}
      {coolingDown && !message && (
        <p className="text-xs text-(--warning)">You can try again at {fmtTime(until!)}.</p>
      )}
      <Button type="submit" variant="primary" loading={submitting}
        disabled={coolingDown || !email || !password}>
        {coolingDown ? `Try again at ${fmtTime(until!)}` : 'Connect Garmin'}
      </Button>
      <p className="flex items-start gap-1.5 text-[0.6875rem] text-(--text-muted)">
        <Lock className="mt-0.5 size-3 shrink-0" />
        Your password goes to Garmin once to sign in and is never stored. Rabbit keeps only
        Garmin's encrypted access token, and you can disconnect anytime.
      </p>
    </form>
  )
}
