import { useEffect, useState } from 'react'
import { Bell, LogOut, Target, Watch } from 'lucide-react'
import { Avatar } from '@/components/Avatar'
import { Group, Icon, Row, Switch } from '@/components/settings/Grouped'
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
    try { await disablePush(); setHere(false); setPushMsg(null); onChange() }
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
      setPushMsg(n > 1 ? `On for ${n} devices.` : null)
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
        ? (r.results.every((x) => x.status && x.status < 300) ? 'Test sent. It should arrive in a few seconds.'
          : r.results.map((x) => `${x.host}: ${x.status ?? 'error'}${x.removed ? ' (removed)' : ''}`).join(', '))
        : 'No devices subscribed.')
    } catch (e) {
      setPushMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }


  const pushStatus = !me.push.available ? 'Not configured on the server.'
    : !pushSupported() ? (isStandalone() ? 'Not supported on this device.' : 'Open Rabbit from your Home Screen to turn these on.')
    : here ? `On for this device${me.push.subscriptions > 1 ? ` and ${me.push.subscriptions - 1} other` : ''}`
    : me.push.subscriptions ? `Off here, on for ${me.push.subscriptions} other device${me.push.subscriptions === 1 ? '' : 's'}` : 'Off'
  const canToggle = me.push.available && pushSupported() && here !== null
  const countdown = goalCountdown(goal)

  return (
    <div className="settings flex flex-col gap-6">
      <Group label="Training">
        <Row icon={<Icon><Target /></Icon>} title={goal ? goal.title : 'No goal yet'}
          subtitle={goal ? (countdown ? `${countdown} · What Rabbit knows` : 'What Rabbit knows') : 'Tell Rabbit what you are training for'}
          onClick={onEditGoal} />
      </Group>

      <Group label="Garmin"
        footer={me.garmin.connected ? 'Rabbit can read your activities, sleep and training data. It never changes anything in Garmin.' : undefined}>
        {me.garmin.connected ? (
          <>
            <Row icon={<Icon><Watch /></Icon>} title="Garmin Connect"
              trailing={<span className="flex items-center gap-1.5 text-[0.7647rem] text-(--accent)">
                <span className="size-2 rounded-full bg-(--accent)" />Connected</span>} />
            <Row title="Disconnect Garmin" danger disabled={busy} center onClick={() => void onDisconnect()} />
          </>
        ) : me.garmin.can_connect ? (
          <div className="p-4">
            <p className="mb-3 text-[0.8235rem] text-(--text-secondary)">Connect your Garmin account so Rabbit can see your training.</p>
            <ConnectGarmin cooldownUntil={me.garmin.cooldown_until} onConnected={onChange} />
          </div>
        ) : (
          <Row icon={<Icon tone="muted"><Watch /></Icon>} title="Garmin" subtitle="Connecting isn't available on this server." />
        )}
        {me.garmin.fake && (
          <Row title="Fake Garmin (dev)" subtitle="Try mfa@, wrong@, 429@ or cf@ emails; MFA code 123456" />
        )}
      </Group>

      {me.garmin.connected && me.chat.available && (
        <CoachingSettings me={me} onChange={onChange} onDevDone={onDevDone} />
      )}

      <Group label="Notifications" footer={pushMsg ?? undefined}>
        <Row icon={<Icon><Bell /></Icon>} title="Notifications" subtitle={pushStatus}
          trailing={canToggle ? (
            <Switch label="Notifications on this device" checked={!!here} disabled={busy}
              onChange={(v) => void (v ? onEnablePush() : onDisablePush())} />
          ) : null} />
        {me.push.available && pushSupported() && me.push.subscriptions > 0 && (
          <Row title={<span className="text-(--accent)">Send a test notification</span>} disabled={busy}
            onClick={() => void onTestPush()} trailing={<span />} />
        )}
      </Group>

      <Group label="Account">
        <Row icon={<Avatar name={me.profile.name || me.user_id} picture={me.profile.picture} />}
          title={me.profile.name || me.user_id} subtitle="Signed in with Google" />
        <Row title={<span className="inline-flex items-center gap-1.5"><LogOut className="size-4" /> Sign out</span>}
          danger center onClick={() => void signOut().finally(onChange)} />
      </Group>
    </div>
  )
}
