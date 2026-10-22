import { useState } from 'react';
import { Loader2, AlertTriangle } from 'lucide-react';
import type { ActionField, ClubOperation } from '@workspace/api-client-react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { errorText } from '@/lib/format';

const ID_TYPES = ['member', 'user', 'role', 'channel', 'mentionable'];
const NONE = '__none__';

type Val = string | boolean;

function kind(f: ActionField): 'bool' | 'int' | 'num' | 'choice' | 'id' | 'long' | 'text' {
  const t = (f.type || '').toLowerCase();
  if (t === 'boolean' || t === 'bool') return 'bool';
  if (f.choices && f.choices.length) return 'choice';
  if (t === 'integer' || t === 'int') return 'int';
  if (t === 'number' || t === 'float') return 'num';
  if (ID_TYPES.includes(t)) return 'id';
  if (['long', 'paragraph', 'markdown', 'textarea'].includes(t) || /notes?|summary|description|criteria|outcome|next_action|context|reason/i.test(f.name)) return 'long';
  return 'text';
}

export function OperationDialog({
  op, open, onOpenChange, onSubmit, pending, error,
}: {
  op: ClubOperation | null;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSubmit: (values: Record<string, unknown>, confirmed: boolean) => void;
  pending: boolean;
  error: unknown;
}) {
  return (
    <Dialog open={open} onOpenChange={(o) => !pending && onOpenChange(o)}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        {op && <Inner key={op.id} op={op} onSubmit={onSubmit} pending={pending} error={error} onCancel={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function Inner({ op, onSubmit, pending, error, onCancel }: { op: ClubOperation; onSubmit: (v: Record<string, unknown>, c: boolean) => void; pending: boolean; error: unknown; onCancel: () => void }) {
  const [vals, setVals] = useState<Record<string, Val>>(() =>
    Object.fromEntries(op.fields.map((f) => [f.name, kind(f) === 'bool' ? false : ''])),
  );
  const [errs, setErrs] = useState<Record<string, string>>({});
  const [confirmed, setConfirmed] = useState(false);
  const [sourceKind, setSourceKind] = useState('bot');
  const fields = op.fields.filter((f) => op.id !== 'scout setup' ||
    (f.name !== 'source_bot' && f.name !== 'webhook_id') ||
    (sourceKind === 'bot' ? f.name === 'source_bot' : f.name === 'webhook_id'));
  const set = (n: string, v: Val) => setVals((p) => ({ ...p, [n]: v }));

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const out: Record<string, unknown> = {};
    const ne: Record<string, string> = {};
    for (const f of fields) {
      const k = kind(f);
      const raw = vals[f.name];
      if (k === 'bool') { out[f.name] = !!raw; continue; }
      const s = String(raw ?? '').trim();
      if (!s) { if (f.required) ne[f.name] = 'Required'; continue; }
      if (k === 'int' || k === 'num') {
        const n = Number(s);
        if (!Number.isFinite(n) || (k === 'int' && !Number.isInteger(n))) { ne[f.name] = k === 'int' ? 'Whole number only' : 'Number only'; continue; }
        if (f.minimum != null && n < f.minimum) { ne[f.name] = `At least ${f.minimum}`; continue; }
        if (f.maximum != null && n > f.maximum) { ne[f.name] = `At most ${f.maximum}`; continue; }
        out[f.name] = n;
      } else if (k === 'id') {
        if (!/^\d{15,22}$/.test(s)) { ne[f.name] = 'Enter the exact numeric Discord ID'; continue; }
        out[f.name] = s;
      } else out[f.name] = s;
    }
    setErrs(ne);
    if (Object.keys(ne).length) return;
    onSubmit(out, op.confirmation ? confirmed : false);
  };

  return (
    <form onSubmit={submit} className="space-y-4" data-testid={`form-op-${op.id}`}>
      <DialogHeader>
        <DialogTitle className="font-display">{op.label}</DialogTitle>
        <DialogDescription>{op.description}</DialogDescription>
      </DialogHeader>
      {op.id === 'scout setup' && <div className="space-y-2"><Label htmlFor="trusted-source-kind">Trusted recap source</Label><Select value={sourceKind} onValueChange={setSourceKind}><SelectTrigger id="trusted-source-kind"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="bot">Another bot member</SelectItem><SelectItem value="webhook">Exact webhook ID (never a URL or token)</SelectItem></SelectContent></Select><p className="text-xs text-muted-foreground">Choose exactly one source. Club Agent never changes channel permissions for you.</p></div>}
      {fields.map((f) => {
        const k = kind(f);
        const id = `f-${op.id}-${f.name}`;
        const v = vals[f.name];
        return (
          <div key={f.name} className="space-y-1.5">
            {k === 'bool' ? (
              <div className="flex items-center gap-2">
                <Checkbox id={id} checked={!!v} onCheckedChange={(c) => set(f.name, c === true)} data-testid={`check-${f.name}`} />
                <Label htmlFor={id}>{f.label}{f.required && <span className="text-destructive"> *</span>}</Label>
              </div>
            ) : (
              <>
                <Label htmlFor={id}>{f.label}{f.required && <span className="text-destructive"> *</span>}</Label>
                {k === 'choice' ? (
                  <Select value={String(v) || NONE} onValueChange={(x) => set(f.name, x === NONE ? '' : x)}>
                    <SelectTrigger id={id} data-testid={`select-${f.name}`}><SelectValue placeholder="Choose" /></SelectTrigger>
                    <SelectContent>
                      {!f.required && <SelectItem value={NONE}>Not set</SelectItem>}
                      {f.choices!.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
                    </SelectContent>
                  </Select>
                ) : k === 'long' ? (
                  <Textarea id={id} rows={4} value={String(v)} onChange={(e) => set(f.name, e.target.value)} data-testid={`input-${f.name}`} />
                ) : (
                  <Input
                    id={id}
                    value={String(v)}
                    onChange={(e) => set(f.name, e.target.value)}
                    type={k === 'int' || k === 'num' ? 'number' : 'text'}
                    step={k === 'int' ? 1 : 'any'}
                    min={f.minimum}
                    max={f.maximum}
                    inputMode={k === 'id' ? 'numeric' : undefined}
                    className={k === 'id' ? 'font-mono' : ''}
                    placeholder={k === 'id' ? `Exact ${f.type} ID` : undefined}
                    data-testid={`input-${f.name}`}
                  />
                )}
              </>
            )}
            {k === 'id' && <p className="text-xs text-muted-foreground">Paste the exact Discord ID (Developer Mode, right-click, Copy ID). Names are not searched.</p>}
            {(f.minimum != null || f.maximum != null) && (k === 'int' || k === 'num') && <p className="text-xs text-muted-foreground">Allowed: {f.minimum ?? 'any'} to {f.maximum ?? 'any'}</p>}
            {errs[f.name] && <p className="text-xs font-medium text-destructive" role="alert">{errs[f.name]}</p>}
          </div>
        );
      })}
      {op.confirmation && (
        <label className="flex items-start gap-2 rounded-md border border-accent/60 bg-accent/10 p-3 text-sm">
          <Checkbox checked={confirmed} onCheckedChange={(c) => setConfirmed(c === true)} className="mt-0.5" data-testid="check-confirm" />
          <span>I confirm this change. It writes to club records and is audited.</span>
        </label>
      )}
      {!!error && (
        <div className="flex gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm" role="alert" data-testid="text-action-error">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div>
            <p className="font-medium">{errorText(error)}</p>
            <p className="text-muted-foreground">If the request may have been interrupted, check the existing records before submitting again. It is not retried automatically.</p>
          </div>
        </div>
      )}
      <DialogFooter className="gap-2">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={pending}>Cancel</Button>
        <Button type="submit" disabled={pending || (op.confirmation && !confirmed)} data-testid="button-submit-op">
          {pending && <Loader2 className="animate-spin" />}{pending ? 'Working' : op.label}
        </Button>
      </DialogFooter>
    </form>
  );
}
