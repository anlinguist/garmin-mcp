import { Button } from '@/components/ui/button'
import { RabbitLogo } from '@/components/RabbitLogo'

const ERRORS: Record<string, string> = {
  not_allowed: "That Google account isn't on the invite list. Ask Andrew to add it.",
  expired: 'That sign-in took too long or was interrupted. Please try again.',
  cancelled: 'Sign-in was cancelled.',
  google: "Couldn't reach Google. Please try again.",
  busy: 'Too many sign-ins in progress. Try again in a minute.',
}

export function SignIn({ error }: { error: string | null }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 p-6 text-center">
      <RabbitLogo size={88} />
      <div>
        <h1 className="font-display text-4xl text-(--brand)">Rabbit</h1>
        <p className="mt-2 text-(--text-secondary)">Your training pacer. Prep, reviews, and a nudge when you need it.</p>
      </div>
      {error && (
        <p className="max-w-sm rounded-(--radius) bg-(--danger-subtle) px-3 py-2 text-sm text-(--danger)">
          {ERRORS[error] ?? 'Sign-in failed. Please try again.'}
        </p>
      )}
      <Button asChild variant="primary" className="h-11 px-6 text-sm">
        <a href="/app/auth/google/start">Sign in with Google</a>
      </Button>
    </div>
  )
}
