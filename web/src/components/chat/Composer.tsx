// Ported from Dolly's composer: auto-growing textarea, Enter sends, Shift+Enter newline.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowUp } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Composer({ disabled, onSend, placeholder }: {
  disabled: boolean
  onSend: (text: string) => void
  placeholder: string
}) {
  const [input, setInput] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  const resize = useCallback(() => {
    const ta = ref.current
    if (!ta) return
    ta.style.height = 'auto'
    if (!ta.value) return
    ta.style.height = `${Math.min(ta.scrollHeight, 22 * 5 + 12)}px`
  }, [])
  useEffect(resize, [input, resize])

  const submit = () => {
    if (!input.trim() || disabled) return
    onSend(input)
    setInput('')
  }

  return (
    <div className="border-t border-(--border) bg-(--bg) px-3 pb-[max(10px,env(safe-area-inset-bottom))] pt-2.5">
      <div className="flex items-end gap-2 rounded-(--radius-lg) border border-(--border-strong) bg-(--bg-surface) px-3 py-2 shadow-sm focus-within:border-(--accent)">
        <textarea
          ref={ref}
          rows={1}
          value={input}
          enterKeyHint="send"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
          }}
          placeholder={placeholder}
          className="max-h-[122px] flex-1 resize-none border-none bg-transparent p-0 text-base leading-[22px] text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-0"
          style={{ overflowY: 'auto' }}
        />
        <button
          type="button"
          aria-label="Send"
          onClick={submit}
          disabled={disabled || !input.trim()}
          className={cn('flex size-8 shrink-0 items-center justify-center rounded-full transition-all',
            input.trim() && !disabled ? 'bg-(--accent) text-white active:scale-95' : 'bg-(--bg-inset) text-(--text-muted)')}
        >
          <ArrowUp className="size-4" strokeWidth={2.5} />
        </button>
      </div>
    </div>
  )
}
