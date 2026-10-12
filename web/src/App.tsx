import { useCallback, useEffect, useState } from 'react'
import { AuthError, getMe, type Me } from './lib/api'
import { isIOS, isStandalone } from './lib/push'
import { Chat } from './screens/Chat'
import { InstallPrompt } from './screens/InstallPrompt'
import { SignIn } from './screens/SignIn'
import { RabbitLogo } from './components/RabbitLogo'

type State =
  | { kind: 'loading' }
  | { kind: 'install' }
  | { kind: 'signin'; error: string | null }
  | { kind: 'home'; me: Me }
  | { kind: 'error'; message: string }

export function App() {
  const [state, setState] = useState<State>({ kind: 'loading' })
  const [skipInstall, setSkipInstall] = useState(false)

  const load = useCallback(async () => {
    const params = new URLSearchParams(window.location.search)
    const authError = params.get('auth_error')
    if (authError) window.history.replaceState(null, '', '/app/')
    // Push on iOS only works from the installed (Home Screen) app, and the installed
    // app keeps its own cookies, so sign-in has to happen there too.
    if (isIOS() && !isStandalone() && !skipInstall) {
      setState({ kind: 'install' })
      return
    }
    try {
      setState({ kind: 'home', me: await getMe() })
    } catch (e) {
      if (e instanceof AuthError) setState({ kind: 'signin', error: authError })
      else setState({ kind: 'error', message: (e as Error).message })
    }
  }, [skipInstall])

  useEffect(() => { void load() }, [load])

  return (
    <div className="app-shell">
      {state.kind === 'loading' && (
        <div className="flex flex-1 items-center justify-center"><RabbitLogo size={48} thinking /></div>
      )}
      {state.kind === 'install' && <InstallPrompt onSkip={() => setSkipInstall(true)} />}
      {state.kind === 'signin' && <SignIn error={state.error} />}
      {state.kind === 'home' && <Chat me={state.me} onChange={load} />}
      {state.kind === 'error' && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
          <RabbitLogo size={40} />
          <p className="text-(--text-secondary)">Something went wrong: {state.message}</p>
          <button className="underline" onClick={() => void load()}>Try again</button>
        </div>
      )}
    </div>
  )
}
