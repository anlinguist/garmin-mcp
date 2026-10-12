// Ported from Dolly (web-mcp) Chat.tsx ToolCallCard / ToolCallGroup, with
// coach-friendly labels and no "Native Request" block.
import { memo, useState } from 'react'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { toolLabel } from '@/lib/tool-labels'
import type { ToolCall } from '@/hooks/useCoach'

function StatusIcon({ status }: { status: ToolCall['status'] }) {
  if (status === 'executing') {
    return (
      <svg className="animate-spin text-muted-foreground/80" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
    )
  }
  if (status === 'error') {
    return (
      <svg className="text-[#dc2626]" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
      </svg>
    )
  }
  return (
    <svg className="text-[#16a34a]" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  )
}

function StatusDot({ status }: { status: ToolCall['status'] }) {
  return (
    <div className={cn(
      'flex h-[20px] w-[20px] shrink-0 items-center justify-center rounded-full border border-border shadow-sm transition-colors',
      status === 'done' ? 'border-[#16a34a]/30 bg-[#16a34a]/10'
        : status === 'error' ? 'border-[#dc2626]/30 bg-[#dc2626]/10' : 'bg-background',
    )}>
      <StatusIcon status={status} />
    </div>
  )
}

export const ToolCallCard = memo(function ToolCallCard({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  const Wrapper = expanded ? Card : 'div'
  const hasDetails = toolCall.result != null || toolCall.error || Object.keys(toolCall.params).length > 0
  return (
    <Wrapper className={cn('w-full overflow-hidden transition-all duration-200',
      expanded ? 'my-1 gap-0 border-border bg-background py-0 shadow-sm' : 'border-transparent bg-transparent')}>
      <div
        className={cn('flex w-max max-w-[85%] cursor-pointer select-none items-center gap-2 rounded-(--radius) px-2 py-1.5 transition-colors',
          expanded ? 'w-full max-w-full rounded-b-none border-b border-border/50 bg-secondary/50' : 'border border-transparent hover:bg-secondary/50')}
        onClick={() => hasDetails && setExpanded((p) => !p)}
      >
        <StatusDot status={toolCall.status} />
        <span className={cn('truncate text-xs font-semibold', expanded ? 'text-foreground' : 'text-muted-foreground')}>
          {toolLabel(toolCall.toolName, toolCall.params)}
        </span>
        {hasDetails && <span className="ml-1 text-[10px] text-muted-foreground/50">{expanded ? '▾' : '▸'}</span>}
      </div>
      <div className={`grid transition-[grid-template-rows] duration-200 ease-out ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
        <div className="min-h-0 overflow-hidden">
          <div className="space-y-3 bg-secondary/20 p-3">
            {Object.keys(toolCall.params).length > 0 && (
              <div>
                <span className="mb-1 block pl-1 text-[10px] font-bold uppercase tracking-widest text-muted-foreground">Asked Garmin for</span>
                <pre className="overflow-x-auto rounded-md border border-border bg-background p-2.5 font-mono text-[11px] text-foreground shadow-sm">
                  {JSON.stringify(toolCall.params, null, 2)}
                </pre>
              </div>
            )}
            {(toolCall.result != null || toolCall.error) && (
              <div>
                <span className={`mb-1 block pl-1 text-[10px] font-bold uppercase tracking-widest ${toolCall.error ? 'text-destructive' : 'text-muted-foreground'}`}>
                  {toolCall.error ? 'Problem' : 'What came back'}
                </span>
                <pre className={cn('max-h-48 overflow-auto rounded-md border bg-background p-2.5 font-mono text-[10px] text-muted-foreground shadow-sm',
                  toolCall.error ? 'border-destructive/50 text-destructive' : 'border-border')}>
                  {toolCall.error ?? JSON.stringify(toolCall.result, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>
      </div>
    </Wrapper>
  )
})

export const ToolCallGroup = memo(function ToolCallGroup({ toolCalls }: { toolCalls: ToolCall[] }) {
  const [expanded, setExpanded] = useState(false)
  const running = toolCalls.some((t) => t.status === 'executing')
  const failed = toolCalls.some((t) => t.status === 'error')
  const status: ToolCall['status'] = running ? 'executing' : failed ? 'error' : 'done'
  return (
    <div className="w-full">
      <div className="flex w-max max-w-[85%] cursor-pointer select-none items-center gap-2 rounded-(--radius) border border-transparent px-2 py-1.5 transition-colors hover:bg-secondary/50"
        onClick={() => setExpanded((p) => !p)}>
        <StatusDot status={status} />
        <span className="truncate text-xs font-semibold text-muted-foreground">
          {running ? 'Checking your Garmin data…' : `Checked ${toolCalls.length} things in Garmin`}
        </span>
        <span className="ml-1 text-[10px] text-muted-foreground/50">{expanded ? '▾' : '▸'}</span>
      </div>
      <div className={`grid transition-[grid-template-rows] duration-200 ease-out ${expanded ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'}`}>
        <div className="min-h-0 overflow-hidden">
          <div className="space-y-1 pl-4 pt-1">
            {toolCalls.map((tc) => <ToolCallCard key={tc.id} toolCall={tc} />)}
          </div>
        </div>
      </div>
    </div>
  )
})
