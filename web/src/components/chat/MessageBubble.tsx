// Ported from Dolly (web-mcp) Chat.tsx MessageBubble. react-markdown without
// rehype-raw: raw HTML in model output is not rendered.
import { memo } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import type { ChatMessage } from '@/hooks/useCoach'
import { FileText } from 'lucide-react'
import { attachmentUrl, formatFileSize } from '@/lib/attachments'

const REMARK_PLUGINS = [remarkGfm]

export const MessageBubble = memo(function MessageBubble({ message, showLabel, pressProps }: {
  message: ChatMessage
  showLabel: boolean
  /** long-press / right-click handlers for the context menu (attached to the bubble) */
  pressProps?: React.HTMLAttributes<HTMLDivElement>
}) {
  const isUser = message.role === 'user'
  return (
    <div className={cn('relative flex w-full min-w-0 flex-col gap-1', isUser ? 'items-end' : 'items-start')}>
      {showLabel && (
        <span className={cn('px-1 text-[0.6471rem] font-semibold uppercase tracking-widest text-muted-foreground', isUser ? 'pr-2' : 'pl-1')}>
          {isUser ? 'You' : 'Rabbit'}
        </span>
      )}
      {message.attachments && message.attachments.length > 0 && (
        <div className={cn('flex max-w-[85%] flex-wrap gap-1.5', isUser ? 'justify-end' : 'justify-start')}>
          {message.attachments.map((a) => a.kind === 'image' ? (
            <a key={a.id} href={attachmentUrl(a.id)} target="_blank" rel="noopener"
              className="block overflow-hidden rounded-xl border border-border shadow-sm">
              <img src={attachmentUrl(a.id)} alt={a.name} loading="lazy"
                className="max-h-56 max-w-[220px] object-cover" />
            </a>
          ) : (
            // Dolly's MessageFileCard
            <a key={a.id} href={attachmentUrl(a.id)} target="_blank" rel="noopener"
              className="inline-flex min-w-[140px] max-w-[220px] items-center gap-2 rounded-lg border border-border bg-background p-1.5 shadow-sm transition-colors hover:bg-secondary/50">
              <span className="flex h-7 w-8 shrink-0 items-center justify-center rounded bg-accent font-mono text-[0.5294rem] font-bold tracking-wide text-white">
                {a.kind === 'pdf' ? 'PDF' : <FileText className="size-3.5" />}
              </span>
              <span className="flex min-w-0 flex-col pr-2">
                <span className="truncate text-xs font-medium text-foreground">{a.name}</span>
                <span className="mt-0.5 font-mono text-[0.5882rem] leading-none text-muted-foreground">{formatFileSize(a.size)}</span>
              </span>
            </a>
          ))}
        </div>
      )}
      {message.text && <div data-msg-id={message.id} {...pressProps} className={cn(
        'msg-press chat-text relative min-w-0 max-w-[85%] overflow-hidden break-words rounded-xl px-3.5 py-2.5 text-[1rem] leading-[1.45] shadow-sm',
        isUser
          ? 'rounded-tr-sm border border-black/10 bg-[var(--msg-user-bg)] text-[var(--msg-user-text)]'
          : message.error
            ? 'rounded-tl-sm border border-(--danger)/30 bg-(--danger-subtle) text-(--danger)'
            : 'rounded-tl-sm border border-border bg-background text-foreground',
      )}>
        {isUser || message.error ? (
          <p className="m-0 whitespace-pre-wrap">{message.text}</p>
        ) : (
          <div className="prose prose-sm text-[1em]! max-w-none overflow-x-auto text-foreground prose-p:my-1.5 prose-p:leading-[1.45] prose-pre:my-2 prose-pre:border prose-pre:border-border prose-pre:bg-secondary/50 prose-table:text-[0.7647rem] prose-headings:text-foreground prose-strong:text-foreground">
            <Markdown remarkPlugins={REMARK_PLUGINS}>{message.text}</Markdown>
          </div>
        )}
      </div>}
    </div>
  )
})
