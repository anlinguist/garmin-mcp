import { useState } from 'react'
import { Bell, LogOut, Radio, Watch } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Avatar } from '@/components/Avatar'
import { RabbitLogo } from '@/components/RabbitLogo'
import { postStream, signOut, type Me } from '@/lib/api'
import { enablePush, isStandalone, pushSupported, sendTestPush } from '@/lib/push'

// Phase 1 home: account status plus the Phase 0 device checks (push, streaming).
// The chat replaces most of this in Phase 3.
export function Home({ me, onChange }: { me: Me; onChange: () => void }) {
  const [pushMsg, setPushMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [ticks, setTicks] = useState<string[]>([])

  async function onEnablePush() {
    if (!me.push.public_key) return
    setBusy(true)
    try {
      const n = await enablePush(me.push.public_key)
      setPushMsg(`Notifications on (${n} device${n === 1 ? '' : 's'}).`)
      onChange()
    } catch (e) {
      setPushMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function onTestPush() {
    setBusy(true)
    try {
      const r = await sendTestPush()
      setPushMsg(r.results.length
        ? r.results.map((x) => `${x.host}: ${x.status ?? 'error'}${x.removed ? ' (removed)' : ''}`).join(', ')
        : 'No devices subscribed.')
    } catch (e) {
      setPushMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function onStreamTest() {
    setTicks([])
    const start = performance.now()
    const r = await postStream('/api/diag/stream')
    const reader = r.body!.getReader()
    const dec = new TextDecoder()
    let buf = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let i
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim()
        buf = buf.slice(i + 1)
        if (line) setTicks((t) => [...t, `${((performance.now() - start) / 1000).toFixed(1)}s  ${line}`])
      }
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex items-center gap-2 border-b border-(--border) px-4 py-3">
        <RabbitLogo size={26} />
        <span className="font-display text-xl text-(--brand)">Rabbit</span>
        <span className="ml-auto"><Avatar name={me.profile.name || me.user_id} picture={me.profile.picture} /></span>
        <Button variant="ghost" size="icon" aria-label="Sign out"
          onClick={() => void signOut().finally(onChange)}><LogOut className="size-4" /></Button>
      </header>

      <main className="mx-auto flex w-full max-w-md flex-col gap-4 p-4">
        <section className="panel flex items-start gap-3">
          <Watch className="mt-0.5 size-5 text-(--accent)" />
          <div>
            <h2 className="font-medium">Garmin</h2>
            <p className="text-sm text-(--text-secondary)">
              {me.garmin_connected ? 'Connected.' : 'Not connected yet. Connecting Garmin comes in the next update.'}
            </p>
          </div>
        </section>

        <section className="panel flex flex-col gap-3">
          <div className="flex items-start gap-3">
            <Bell className="mt-0.5 size-5 text-(--accent)" />
            <div>
              <h2 className="font-medium">Notifications</h2>
              <p className="text-sm text-(--text-secondary)">
                {!me.push.available ? 'Not configured on the server.'
                  : !pushSupported() ? (isStandalone() ? 'Not supported on this device.' : 'Open Rabbit from your Home Screen to turn these on.')
                  : me.push.subscriptions ? `On for ${me.push.subscriptions} device(s).` : 'Off.'}
              </p>
            </div>
          </div>
          {me.push.available && pushSupported() && (
            <div className="flex gap-2">
              <Button variant="primary" disabled={busy} onClick={() => void onEnablePush()}>Turn on notifications</Button>
              <Button disabled={busy || !me.push.subscriptions} onClick={() => void onTestPush()}>Send test</Button>
            </div>
          )}
          {pushMsg && <p className="text-xs text-(--text-muted)">{pushMsg}</p>}
        </section>

        <section className="panel flex flex-col gap-3">
          <div className="flex items-start gap-3">
            <Radio className="mt-0.5 size-5 text-(--accent)" />
            <div>
              <h2 className="font-medium">Streaming check</h2>
              <p className="text-sm text-(--text-secondary)">Lines should arrive about every 0.5s, not all at once.</p>
            </div>
          </div>
          <Button className="self-start" onClick={() => void onStreamTest()}>Run</Button>
          {ticks.length > 0 && (
            <pre className="max-h-48 overflow-auto rounded-(--radius) bg-(--bg-inset) p-2 text-[11px]">{ticks.join('\n')}</pre>
          )}
        </section>
      </main>
    </div>
  )
}
