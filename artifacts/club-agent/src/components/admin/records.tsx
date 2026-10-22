import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { EvidenceBlock, RecordRow } from '@workspace/api-client-react';
import { Badge } from '@/components/ui/badge';
import { Markdown, Value, humanize } from '@/lib/format';
import { cn } from '@/lib/utils';

const TITLE_KEYS = ['name', 'title', 'label', 'player', 'member', 'display_name', 'displayName', 'summary', 'event', 'id'];
const STATUS_KEYS = ['status', 'state', 'tier', 'stage', 'outcome'];

export function RecordCard({ row, index }: { row: RecordRow; index: number }) {
  const [open, setOpen] = useState(false);
  const entries = Object.entries(row).filter(([, v]) => v !== undefined);
  const titleKey = TITLE_KEYS.find((k) => row[k] != null && typeof row[k] !== 'object');
  const statusKey = STATUS_KEYS.find((k) => row[k] != null && typeof row[k] !== 'object');
  const rest = entries.filter(([k]) => k !== titleKey && k !== statusKey);
  const shown = open ? rest : rest.slice(0, 6);
  return (
    <article className="rounded-lg border bg-card p-3 transition-shadow hover:shadow-md" data-testid={`record-${index}`}>
      <header className="mb-2 flex items-start justify-between gap-2">
        <h3 className="min-w-0 text-sm font-semibold [overflow-wrap:anywhere]">
          {titleKey ? <Value v={row[titleKey]} k={titleKey} /> : <span className="text-muted-foreground">Record {index + 1}</span>}
        </h3>
        {statusKey && <Badge variant="secondary" className="shrink-0 capitalize">{String(row[statusKey])}</Badge>}
      </header>
      <dl className="grid grid-cols-[minmax(5rem,auto)_1fr] gap-x-3 gap-y-1 text-[13px]">
        {shown.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-xs text-muted-foreground pt-0.5">{humanize(k)}</dt>
            <dd className="min-w-0 [overflow-wrap:anywhere]"><Value v={v} k={k} /></dd>
          </div>
        ))}
      </dl>
      {rest.length > 6 && (
        <button onClick={() => setOpen((o) => !o)} className="mt-2 flex items-center gap-1 text-xs font-medium text-[hsl(214_80%_38%)]" data-testid={`button-expand-${index}`}>
          <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')} />
          {open ? 'Show fewer fields' : `Show ${rest.length - 6} more fields`}
        </button>
      )}
    </article>
  );
}

export function EvidenceBlocks({ blocks, className }: { blocks: EvidenceBlock[]; className?: string }) {
  if (!blocks?.length) return null;
  return (
    <div className={cn('space-y-3', className)}>
      {blocks.map((b, i) => (
        <section key={i} className="rounded-lg border-l-4 border-l-accent border bg-card p-4" data-testid={`block-${i}`}>
          {b.title && <h3 className="mb-2 font-display text-sm font-bold tracking-wide">{b.title}</h3>}
          <Markdown text={b.text} />
        </section>
      ))}
    </div>
  );
}
