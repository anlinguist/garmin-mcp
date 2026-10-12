// Ported from Dolly's composer (web-mcp): drag-and-drop zone, attachment chips,
// auto-growing textarea, round send button that turns into a red stop button.
// Added for Rabbit: image paste, image thumbnails, uploads on add.
import { useCallback, useEffect, useRef, useState } from 'react'
import { FileText, Paperclip, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ACCEPT, MAX_FILES, formatFileSize, uploadAttachment, type AttachmentMeta } from '@/lib/attachments'

type Pending = {
  localId: string; name: string; size: number; isImage: boolean; preview?: string
  status: 'uploading' | 'ready' | 'error'; meta?: AttachmentMeta; error?: string
}

let n = 0

export const ATTACH_INPUT_ID = 'rabbit-attach'


export function Composer({ disabled, streaming, onSend, onStop, placeholder, draft }: {
  disabled: boolean
  streaming: boolean
  onSend: (text: string, attachments: AttachmentMeta[]) => void
  onStop: () => void
  placeholder: string
  /** prefill from elsewhere ("Tell Rabbit…"); bump n to apply again */
  draft?: { text: string; n: number } | null
}) {
  const [input, setInput] = useState('')
  const [files, setFiles] = useState<Pending[]>([])
  const [dragging, setDragging] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const ref = useRef<HTMLTextAreaElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)

  const resize = useCallback(() => {
    const ta = ref.current
    if (!ta) return
    ta.style.height = 'auto'
    if (!ta.value) return
    const line = parseFloat(getComputedStyle(ta).lineHeight) || 22
    ta.style.height = `${Math.min(ta.scrollHeight, line * 5 + 12)}px`
  }, [])
  useEffect(resize, [input, resize])
  useEffect(() => {
    if (!draft) return
    setInput(draft.text)
    requestAnimationFrame(() => {
      const ta = ref.current
      if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length) }
    })
  }, [draft?.n]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => files.forEach((f) => f.preview && URL.revokeObjectURL(f.preview)), []) // eslint-disable-line react-hooks/exhaustive-deps

  const filesRef = useRef<Pending[]>([])
  useEffect(() => { filesRef.current = files }, [files])

  const addFiles = useCallback((list: File[]) => {
    setNotice(null)
    const room = MAX_FILES - filesRef.current.length
    if (list.length > room) setNotice(`Up to ${MAX_FILES} attachments per message.`)
    const added: Pending[] = list.slice(0, Math.max(0, room)).map((file) => {
      const isImage = file.type.startsWith('image/') || /\.(heic|heif)$/i.test(file.name)
      return {
        localId: `f${n++}`, name: file.name || (isImage ? 'Pasted image' : 'File'), size: file.size,
        isImage, preview: isImage ? URL.createObjectURL(file) : undefined, status: 'uploading',
      }
    })
    filesRef.current = [...filesRef.current, ...added]
    setFiles((prev) => [...prev, ...added])
    // Upload outside the state updater (React dev mode runs updaters twice).
    added.forEach((p, i) => {
      uploadAttachment(list[i]!).then(
        (meta) => setFiles((cur) => cur.map((f) => (f.localId === p.localId ? { ...f, status: 'ready', meta } : f))),
        (err: Error) => setFiles((cur) => cur.map((f) => (f.localId === p.localId ? { ...f, status: 'error', error: err.message } : f))),
      )
    })
  }, [])

  const remove = (id: string) => setFiles((cur) => {
    const f = cur.find((x) => x.localId === id)
    if (f?.preview) URL.revokeObjectURL(f.preview)
    return cur.filter((x) => x.localId !== id)
  })

  const uploading = files.some((f) => f.status === 'uploading')
  const ready = files.filter((f) => f.status === 'ready' && f.meta).map((f) => f.meta!)
  const canSend = !disabled && !uploading && (input.trim().length > 0 || ready.length > 0)

  const submit = () => {
    if (!canSend) return
    onSend(input, ready)
    setInput('')
    files.forEach((f) => f.preview && URL.revokeObjectURL(f.preview))
    setFiles([])
    setNotice(null)
  }

  return (
    <div
      className={cn('composer-bar relative z-40 shrink-0 border-t border-(--border) bg-(--bg) px-3 pb-[max(8px,env(safe-area-inset-bottom))] pt-2 transition-colors duration-200',
        dragging && 'border-accent bg-accent/5')}
      onDragEnter={(e) => { if (e.dataTransfer.types.includes('Files')) { dragDepth.current++; setDragging(true) } }}
      onDragLeave={() => { dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false) }}
      onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) e.preventDefault() }}
      onDrop={(e) => { e.preventDefault(); dragDepth.current = 0; setDragging(false); addFiles(Array.from(e.dataTransfer.files)) }}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-30 flex items-center justify-center gap-2 border-2 border-dashed border-accent bg-accent/5 text-sm font-semibold text-accent">
          <Paperclip className="size-5" /> Drop files to attach
        </div>
      )}

      {files.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-2">
          {files.map((f) => f.isImage && f.preview ? (
            <div key={f.localId} className="relative size-16 overflow-hidden rounded-lg border border-border bg-secondary">
              <img src={f.preview} alt={f.name} className={cn('size-full object-cover', f.status === 'uploading' && 'opacity-50')} />
              {f.status === 'uploading' && <span className="absolute inset-0 flex items-center justify-center"><span className="thinking-dots flex gap-[3px]"><span /><span /><span /></span></span>}
              {f.status === 'error' && <span className="absolute inset-x-0 bottom-0 bg-(--danger) px-1 text-[0.5294rem] text-white" title={f.error}>Failed</span>}
              <button aria-label="Remove" onClick={() => remove(f.localId)}
                className="absolute right-0.5 top-0.5 flex size-5 items-center justify-center rounded-full bg-black/60 text-white">
                <X className="size-3" />
              </button>
            </div>
          ) : (
            <div key={f.localId} title={f.error}
              className={cn('group inline-flex items-center gap-1.5 rounded border bg-secondary px-2 py-1 text-xs transition-colors',
                f.status === 'error' ? 'border-(--danger)/50' : 'border-border')}>
              <FileText className="size-3 shrink-0 text-accent" />
              <span className="max-w-[120px] truncate font-medium text-foreground/80">{f.name}</span>
              <span className="font-mono text-[0.6471rem] text-muted-foreground">
                {f.status === 'uploading' ? '…' : f.status === 'error' ? 'failed' : formatFileSize(f.size)}
              </span>
              <button aria-label="Remove" onClick={() => remove(f.localId)}
                className="flex size-[18px] items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive">
                <X className="size-2.5" />
              </button>
            </div>
          ))}
        </div>
      )}
      {(notice || files.some((f) => f.status === 'error')) && (
        <p className="mb-1.5 text-[0.6471rem] text-(--danger)">{notice ?? files.find((f) => f.error)?.error}</p>
      )}

      <div className="flex items-end gap-2 rounded-(--radius-lg) border border-(--border-strong) bg-(--bg-surface) py-1.5 pl-1.5 pr-1.5 shadow-sm focus-within:border-(--accent)">
        <button type="button" aria-label="Attach" onClick={() => picker.current?.click()}
          disabled={files.length >= MAX_FILES}
          className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:opacity-40">
          <Paperclip className="size-[18px]" />
        </button>
        {/* id lets "Upload my plan" buttons elsewhere open this picker via <label htmlFor>,
            which keeps the tap a real user gesture (iOS won't open pickers otherwise) */}
        <input ref={picker} id={ATTACH_INPUT_ID} type="file" className="hidden" multiple accept={ACCEPT}
          onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = '' }} />
        <textarea
          ref={ref}
          rows={1}
          value={input}
          enterKeyHint="send"
          onChange={(e) => setInput(e.target.value)}
          onPaste={(e) => {
            const pasted = Array.from(e.clipboardData.files)
            if (pasted.length) { e.preventDefault(); addFiles(pasted) }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
          }}
          placeholder={placeholder}
          className="chat-text max-h-[calc(1.375em*5+12px)] flex-1 resize-none self-center border-none bg-transparent p-0 py-[5px] text-base leading-[1.375] text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-0"
          style={{ overflowY: 'auto' }}
        />
        {streaming ? (
          <button type="button" aria-label="Stop" onClick={onStop}
            className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full border-none bg-destructive text-white shadow-sm transition-all hover:opacity-90 active:scale-95">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" ry="2" /></svg>
          </button>
        ) : (
          <button type="button" aria-label="Send" onClick={submit} disabled={!canSend}
            className={cn('flex size-9 shrink-0 items-center justify-center rounded-full border-none shadow-sm transition-all',
              canSend
                ? 'cursor-pointer bg-[var(--btn-approve-bg)] text-[var(--btn-approve-text)] hover:scale-105 hover:opacity-90 active:scale-95'
                : 'cursor-not-allowed bg-[var(--bg-elevated)] text-muted-foreground shadow-none')}>
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 19V5" /><path d="m5 12 7-7 7 7" /></svg>
          </button>
        )}
      </div>
    </div>
  )
}
