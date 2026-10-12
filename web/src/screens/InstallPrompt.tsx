import { Share, SquarePlus } from 'lucide-react'
import { RabbitLogo } from '@/components/RabbitLogo'

export function InstallPrompt({ onSkip }: { onSkip: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 p-6 text-center">
      <RabbitLogo size={72} />
      <div>
        <h1 className="font-display text-3xl text-(--brand)">Rabbit</h1>
        <p className="mt-1 text-(--text-secondary)">Add Rabbit to your Home Screen to get started.</p>
      </div>
      <ol className="panel w-full max-w-sm space-y-4 text-left">
        <li className="flex items-center gap-3">
          <span className="font-display text-xl text-(--accent)">1</span>
          <span>Tap <Share className="inline size-4 align-text-bottom" aria-label="Share" /> Share in Safari's toolbar.</span>
        </li>
        <li className="flex items-center gap-3">
          <span className="font-display text-xl text-(--accent)">2</span>
          <span>Choose <SquarePlus className="inline size-4 align-text-bottom" /> <b>Add to Home Screen</b>.</span>
        </li>
        <li className="flex items-center gap-3">
          <span className="font-display text-xl text-(--accent)">3</span>
          <span>Open <b>Rabbit</b> from your Home Screen and sign in there.</span>
        </li>
      </ol>
      <p className="max-w-sm text-xs text-(--text-muted)">
        Notifications only work from the Home Screen app, so sign in from there rather than in Safari.
      </p>
      <button className="text-xs text-(--text-muted) underline" onClick={onSkip}>Continue in the browser anyway</button>
    </div>
  )
}
