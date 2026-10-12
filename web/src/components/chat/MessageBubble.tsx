// Ported from Dolly (web-mcp) Chat.tsx MessageBubble. react-markdown without
// rehype-raw: raw HTML in model output is not rendered.
import { memo } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { cn } from '@/lib/utils'
import type { ChatMessage } from '@/hooks/useCoach'

const REMARK_PLUGINS = [remarkGfm]

export const MessageBubble = memo(function MessageBubble({ message, showLabel }: {
  message: ChatMessage
  showLabel: boolean
}) {
  const isUser = message.role === 'user'
  return (
    <div className={cn('relative flex w-full min-w-0 flex-col gap-1', isUser ? 'items-end' : 'items-start')}>
      {showLabel && (
        <span className={cn('px-1 text-[11px] font-semibold uppercase tracking-widest text-muted-foreground', isUser ? 'pr-2' : 'pl-1')}>
          {isUser ? 'You' : 'Rabbit'}
        </span>
      )}
      <div className={cn(
        'relative min-w-0 max-w-[85%] overflow-hidden break-words rounded-xl px-3.5 py-2.5 text-[14px] leading-relaxed shadow-sm',
        isUser
          ? 'rounded-tr-sm border border-black/10 bg-[var(--msg-user-bg)] text-[var(--msg-user-text)]'
          : message.error
            ? 'rounded-tl-sm border border-(--danger)/30 bg-(--danger-subtle) text-(--danger)'
            : 'rounded-tl-sm border border-border bg-background text-foreground',
      )}>
        {isUser || message.error ? (
          <p className="m-0 whitespace-pre-wrap">{message.text}</p>
        ) : (
          <div className="prose prose-sm max-w-none overflow-x-auto text-foreground prose-p:my-1.5 prose-p:leading-relaxed prose-pre:my-2 prose-pre:border prose-pre:border-border prose-pre:bg-secondary/50 prose-table:text-[13px] prose-headings:text-foreground prose-strong:text-foreground">
            <Markdown remarkPlugins={REMARK_PLUGINS}>{message.text}</Markdown>
          </div>
        )}
      </div>
    </div>
  )
})
