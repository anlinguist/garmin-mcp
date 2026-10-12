// iMessage-style context menu for a message: the pressed bubble lifts above a blurred,
// dimmed backdrop and a menu pops out from its edge.
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Copy, MessagesSquare, Share, TextSelect, X } from 'lucide-react'
import { useSheetDrag } from '@/hooks/useSheetDrag'
import type { ChatMessage } from '@/hooks/useCoach'

export type MenuTarget = { item: ChatMessage; el: HTMLElement; canDiscuss: boolean }

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = Object.assign(document.createElement('textarea'), { value: text })
    ta.style.position = 'fixed'; ta.style.opacity = '0'
    document.body.appendChild(ta); ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  }
}

export function MessageMenu({ target, onClose, onDiscuss, onToast }: {
  target: MenuTarget
  onClose: () => void
  onDiscuss: () => void
  onToast: (msg: string) => void
}) {
  const menu = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; left?: number; right?: number; origin: string } | null>(null)
  const [selecting, setSelecting] = useState(false)
  const isUser = target.item.role === 'user'
  const text = target.item.text

  // Lift the pressed bubble above the backdrop while the menu is open; drop it once the
  // Select-text sheet takes over (a lifted bubble would sit on top of the sheet).
  useEffect(() => {
    if (selecting) return
    const el = target.el
    el.classList.add('msg-lifted')
    return () => el.classList.remove('msg-lifted')
  }, [target.el, selecting])

  useLayoutEffect(() => {
    const r = target.el.getBoundingClientRect()
    const h = menu.current?.offsetHeight ?? 180
    const below = r.bottom + 10 + h < window.innerHeight - 16
    const top = below ? r.bottom + 10 : Math.max(16, r.top - 10 - h)
    const origin = `${isUser ? 'right' : 'left'} ${below ? 'top' : 'bottom'}`
    setPos(isUser ? { top, right: Math.max(12, window.innerWidth - r.right), origin }
      : { top, left: Math.max(12, r.left), origin })
  }, [target.el, isUser])

  const act = (fn: () => void | Promise<void>) => async () => { await fn(); if (!selecting) onClose() }
  const canShare = typeof navigator.share === 'function'

  if (selecting) return <SelectSheet text={text} onClose={onClose} />

  return (
    <>
      <div className="msg-menu-backdrop" onClick={onClose} onContextMenu={(e) => { e.preventDefault(); onClose() }} />
      <div ref={menu} role="menu" className="msg-menu"
        style={pos ? { top: pos.top, left: pos.left, right: pos.right, transformOrigin: pos.origin } : { visibility: 'hidden', top: 0 }}>
        <MenuItem icon={<Copy className="size-4" />} label="Copy"
          onClick={act(async () => { onToast((await copyText(text)) ? 'Copied' : "Couldn't copy") })} />
        <MenuItem icon={<TextSelect className="size-4" />} label="Select text" onClick={() => setSelecting(true)} />
        {canShare && (
          <MenuItem icon={<Share className="size-4" />} label="Share"
            onClick={act(async () => { await navigator.share({ text }).catch(() => {}) })} />
        )}
        {target.canDiscuss && (
          <MenuItem icon={<MessagesSquare className="size-4" />} label="Discuss" onClick={act(onDiscuss)} />
        )}
      </div>
    </>
  )
}

function MenuItem({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button role="menuitem" onClick={onClick}
      className="flex w-full items-center justify-between gap-6 px-4 py-2.5 text-left text-[15px] text-(--text) active:bg-(--bg-inset)">
      {label}<span className="text-(--text-secondary)">{icon}</span>
    </button>
  )
}

/** Plain selectable text, for copying part of a message. */
function SelectSheet({ text, onClose }: { text: string; onClose: () => void }) {
  // Drag-to-dismiss from the handle/header only: in the body, drags extend the selection.
  const drag = useSheetDrag(onClose, { bodyDrags: false })
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()} {...drag}>
        <div className="sheet-handle" />
        <div className="modal-header">
          <h2 className="modal-title">Select text</h2>
          <button className="modal-close" aria-label="Close" onClick={onClose}><X className="size-4" /></button>
        </div>
        <div className="modal-body selectable overflow-y-auto whitespace-pre-wrap p-4 pb-[max(16px,env(safe-area-inset-bottom))] text-[15px] leading-relaxed">
          {text}
        </div>
      </div>
    </div>
  )
}
