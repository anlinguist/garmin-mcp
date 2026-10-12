import { useEffect, useState } from 'react'
import { Bell, LogOut, Target, Watch } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { CoachingSettings } from '@/components/CoachingSettings'
import { ConnectGarmin } from '@/components/ConnectGarmin'
import { goalCountdown, postJSON, signOut, type Goal, type Me } from '@/lib/api'
import { disablePush, enablePush, isStandalone, pushSupported, sendTestPush, thisDeviceSubscribed } from '@/lib/push'

// Settings sheet content: Garmin connection, notifications, device checks, account.
export function SettingsPanels({ me, goal, onEditGoal, onChange, onDevDone }: {
  me: Me
  goal: Goal | null
  onEditGoal: () => void
  onChange: () => void
  onDevDone: () => void
}) {
  const [pushMsg, setPushMsg] = useState<string | null>(null)
  const [here, setHere] = useState<boolean | null>(null) // this device subscribed?
  useEffect(() => { void thisDeviceSubscribed().then(setHere) }, [me.push.subscriptions])

  async function onDisablePush() {
    setBusy(true)
    try { await disablePush(); setHere(false); setPushMsg('Notifications off on this device.'); onChange() }
    catch (e) { setPushMsg((e as Error).message) } finally { setBusy(false) }
  }
  const [busy, setBusy] = useState(false)

  async function onDisconnect() {
    if (!window.confirm('Disconnect Garmin? Rabbit will stop seeing your training data until you connect again.')) return
    setBusy(true)
    try { await postJSON('/api/garmin/disconnect') } finally { setBusy(false); onChange() }
  }

  async function onEnablePush() {
    if (!me.push.public_key) return
    setBusy(true)
    try {
      const n = await enablePush(me.push.public_key)
      setHere(true)
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


  return (
    <div className="flex flex-col gap-4">
        <section className="panel flex items-start gap-3">
          <Target className="mt-0.5 size-5 text-(--accent)" />
          <div className="min-w-0 flex-1">
            <h2 className="font-medium">Goal</h2>
            <p className="truncate text-sm text-(--text-secondary)">
              {goal ? `${goal.title}${goalCountdown(goal) ? ` · ${goalCountdown(goal)}` : ''}` : 'Not set yet'}
            </p>
          </div>
          <Button onClick={onEditGoal}>What Rabbit knows</Button>
        </section>
        <section className="panel flex flex-col gap-3">
          <div className="flex items-start gap-3">
            <Watch className="mt-0.5 size-5 text-(--accent)" />
            <div className="flex-1">
              <h2 className="font-medium">Garmin</h2>
              <p className="text-sm text-(--text-secondary)">
                {me.garmin.connected ? 'Connected. Rabbit can read your activities, sleep and training data.'
                  : 'Connect your Garmin account so Rabbit can see your training.'}
              </p>
            </div>
            {me.garmin.fake && (
              <span className="rounded-full bg-(--warning-subtle) px-2 py-0.5 text-[10px] text-(--warning)"
                title="Dev mode: try mfa@, wrong@, 429@ or cf@ emails; MFA code 123456">FAKE</span>
            )}
          </div>
          {me.garmin.connected ? (
            <Button variant="ghostDanger" className="self-start" disabled={busy}
              onClick={() => void onDisconnect()}>Disconnect Garmin</Button>
          ) : me.garmin.can_connect ? (
            <ConnectGarmin cooldownUntil={me.garmin.cooldown_until} onConnected={onChange} />
          ) : (
            <p className="text-xs text-(--text-muted)">Garmin connect isn't available on this server.</p>
          )}
        </section>

        {me.garmin.connected && me.chat.available && (
          <CoachingSettings me={me} onChange={onChange} onDevDone={onDevDone} />
        )}

        <section className="panel flex flex-col gap-3">
          <div className="flex items-start gap-3">
            <Bell className="mt-0.5 size-5 text-(--accent)" />
            <div>
              <h2 className="font-medium">Notifications</h2>
              <p className="text-sm text-(--text-secondary)">
                {!me.push.available ? 'Not configured on the server.'
                  : !pushSupported() ? (isStandalone() ? 'Not supported on this device.' : 'Open Rabbit from your Home Screen to turn these on.')
                  : here ? `On for this device${me.push.subscriptions > 1 ? ` (and ${me.push.subscriptions - 1} other)` : ''}.`
                  : me.push.subscriptions ? `Off on this device (on for ${me.push.subscriptions} other).` : 'Off.'}
              </p>
            </div>
          </div>
          {me.push.available && pushSupported() && (
            <div className="flex gap-2">
              {here === false && (
                <Button variant="primary" disabled={busy} onClick={() => void onEnablePush()}>Turn on notifications</Button>
              )}
              <Button disabled={busy || !me.push.subscriptions} onClick={() => void onTestPush()}>Send test</Button>
              {here && <Button variant="ghost" disabled={busy} onClick={() => void onDisablePush()}>Turn off</Button>}
            </div>
          )}
          {pushMsg && <p className="text-xs text-(--text-muted)">{pushMsg}</p>}
        </section>

        <section className="panel flex flex-col gap-2">
          <h2 className="font-medium">Account</h2>
          <p className="text-sm text-(--text-secondary)">Signed in as {me.profile.name || me.user_id}.</p>
          <Button variant="outline" onClick={() => void signOut().finally(onChange)}
            className="h-10 w-full border-(--danger) bg-transparent text-(--danger) shadow-none hover:bg-(--danger-subtle) hover:text-(--danger)">
            <LogOut className="mr-1.5 size-4" /> Sign out
          </Button>
        </section>
    </div>
  )
}
