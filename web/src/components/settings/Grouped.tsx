// iOS-style grouped list for the Settings sheet: a small section label, a rounded group
// of rows separated by hairlines, and an optional footnote under the group.
import { ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Group({ label, footer, children }: {
  label?: string; footer?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-1.5">
      {label && (
        <h2 className="px-4 font-mono text-[0.6471rem] font-semibold uppercase tracking-widest text-(--text-muted)">{label}</h2>
      )}
      <div className="settings-group overflow-hidden rounded-xl border border-(--border) bg-(--bg-surface)">{children}</div>
      {footer && <div className="px-4 text-[0.7647rem] leading-snug text-(--text-muted)">{footer}</div>}
    </section>
  )
}

export function Icon({ children, tone = 'accent' }: { children: React.ReactNode; tone?: 'accent' | 'danger' | 'muted' }) {
  return (
    <span className={cn('flex size-[30px] shrink-0 items-center justify-center rounded-lg [&>svg]:size-[17px]',
      tone === 'accent' ? 'bg-(--accent-subtle) text-(--accent)'
        : tone === 'danger' ? 'bg-(--danger-subtle) text-(--danger)'
        : 'bg-(--bg-inset) text-(--text-secondary)')}>
      {children}
    </span>
  )
}

/** One row. Clickable when `onClick` is set (chevron unless `trailing` is given). */
export function Row({ icon, title, subtitle, trailing, onClick, danger, disabled, center }: {
  icon?: React.ReactNode; title: React.ReactNode; subtitle?: React.ReactNode
  trailing?: React.ReactNode; onClick?: () => void; danger?: boolean; disabled?: boolean; center?: boolean
}) {
  const body = (
    <>
      {icon}
      <span className={cn('min-w-0 flex-1', center && 'text-center')}>
        <span className={cn('block text-[0.9412rem] leading-snug', danger ? 'text-(--danger)' : 'text-(--text)')}>{title}</span>
        {subtitle && <span className="mt-0.5 block text-[0.7647rem] leading-snug text-(--text-muted)">{subtitle}</span>}
      </span>
      {trailing ?? (onClick && !center && <ChevronRight className="size-4 shrink-0 text-(--text-muted)" />)}
    </>
  )
  const cls = 'settings-row flex w-full items-center gap-3 px-4 py-3 text-left'
  return onClick ? (
    <button type="button" onClick={onClick} disabled={disabled}
      className={cn(cls, 'transition-colors active:bg-(--bg-inset) disabled:opacity-50')}>{body}</button>
  ) : <div className={cls}>{body}</div>
}

export function Switch({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn('relative h-[31px] w-[51px] shrink-0 rounded-full transition-colors duration-200 disabled:opacity-50',
        checked ? 'bg-(--accent)' : 'bg-(--bg-inset)')}>
      <span className={cn('absolute top-[2px] left-[2px] size-[27px] rounded-full bg-white shadow-md transition-transform duration-200',
        checked && 'translate-x-[20px]')} />
    </button>
  )
}
