import { useMemo } from 'react';
import { Play, ShieldAlert } from 'lucide-react';
import type { ClubOperation } from '@workspace/api-client-react';
import { groupLabel } from '@/lib/screens';

export function OperationsPanel({ ops, onOpen, hasDraft }: { ops: ClubOperation[]; onOpen: (op: ClubOperation) => void; hasDraft: boolean }) {
  const groups = useMemo(() => {
    const m = new Map<string, { label: string; ops: ClubOperation[] }>();
    for (const o of ops) {
      const g = groupLabel(o.id);
      if (!m.has(g.key)) m.set(g.key, { label: g.label, ops: [] });
      m.get(g.key)!.ops.push(o);
    }
    return [...m.values()];
  }, [ops]);

  if (!ops.length) {
    return <p className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground" data-testid="text-no-operations">No operations are available on this screen for your role.</p>;
  }
  return (
    <div className="space-y-4">
      {groups.map((g) => (
        <section key={g.label}>
          <h3 className="mb-1.5 font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">{g.label}</h3>
          <ul className="divide-y overflow-hidden rounded-lg border bg-card">
            {g.ops.map((o) => {
              const needsDraft = /draft/i.test(o.id) && /(correct|save|discard)$/i.test(o.id) && !hasDraft;
              return (
                <li key={o.id}>
                  <button
                    onClick={() => onOpen(o)}
                    disabled={needsDraft}
                    className="group flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors hover:bg-accent/15 disabled:cursor-not-allowed disabled:opacity-50"
                    data-testid={`button-op-${o.id}`}
                  >
                    <Play className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[hsl(38_75%_38%)] transition-transform group-hover:translate-x-0.5" />
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5 text-[13px] font-semibold">
                        {o.label}{o.confirmation && <ShieldAlert className="h-3 w-3 text-destructive" aria-label="Requires confirmation" />}
                      </span>
                      <span className="block text-xs text-muted-foreground">{needsDraft ? 'Read or start a draft first.' : o.description}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
