import { useMemo, useState } from 'react';
import { Loader2, AlertTriangle, Save, Trash2, PencilLine } from 'lucide-react';
import type { ClubOperation } from '@workspace/api-client-react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { errorText, humanize, Inline } from '@/lib/format';

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => !!v && typeof v === 'object' && !Array.isArray(v);

interface Cat { key: string; label: string; guidance?: string; min: number; max: number }

function normalizeCats(meta: unknown, container: Obj): Cat[] {
  const mk = (key: string, o: unknown): Cat => {
    const x = isObj(o) ? o : {};
    return {
      key,
      label: String(x.label ?? x.name ?? humanize(key)),
      guidance: x.guidance != null ? String(x.guidance) : x.description != null ? String(x.description) : typeof o === 'string' ? undefined : undefined,
      min: Number(x.minimum ?? x.min ?? 1),
      max: Number(x.maximum ?? x.max ?? 10),
    };
  };
  let cats: Cat[] = [];
  if (Array.isArray(meta)) {
    cats = meta.map((m) => (typeof m === 'string' ? mk(m, {}) : mk(String((m as Obj)?.key ?? (m as Obj)?.id ?? (m as Obj)?.name ?? ''), m))).filter((c) => c.key);
  } else if (isObj(meta)) {
    cats = Object.entries(meta).map(([k, v]) => (typeof v === 'string' ? { ...mk(k, {}), label: v } : mk(k, v)));
  }
  if (!cats.length) {
    cats = Object.entries(container).filter(([, v]) => isObj(v) && 'score' in v).map(([k]) => mk(k, {}));
  }
  return cats;
}

export function EvaluationEditor({
  draft, ops, open, onClose, onRun, pending, error, pendingKind,
}: {
  draft: Obj;
  ops: ClubOperation[];
  open: boolean;
  onClose: () => void;
  onRun: (operation: string, values: Record<string, unknown>, confirmed: boolean, kind: 'correct' | 'save' | 'discard') => void;
  pending: boolean;
  error: unknown;
  pendingKind: string | null;
}) {
  const payload: Obj = isObj(draft.payload) ? draft.payload : {};
  const nested = isObj(payload.categories);
  const container: Obj = nested ? (payload.categories as Obj) : payload;
  const cats = useMemo(() => normalizeCats(draft.categories, container), [draft.categories, container]);

  const [scores, setScores] = useState<Record<string, string>>(() =>
    Object.fromEntries(cats.map((c) => [c.key, isObj(container[c.key]) && container[c.key] != null ? String((container[c.key] as Obj).score ?? '') : ''])));
  const [evidence, setEvidence] = useState<Record<string, string>>(() =>
    Object.fromEntries(cats.map((c) => [c.key, isObj(container[c.key]) ? String((container[c.key] as Obj).evidence ?? '') : ''])));
  const [count, setCount] = useState(payload.count != null ? String(payload.count) : '');
  const [context, setContext] = useState(payload.context != null ? String(payload.context) : '');
  const [notes, setNotes] = useState(payload.notes != null ? String(payload.notes) : '');
  const [confirmSave, setConfirmSave] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [localErr, setLocalErr] = useState<string | null>(null);

  const id = draft.id;
  const revision = draft.revision;
  const find = (suffix: string) => ops.find((o) => /draft/i.test(o.id) && o.id.toLowerCase().endsWith(suffix));

  const assemble = (): Obj | null => {
    const next: Obj = { ...payload };
    const cont: Obj = { ...container };
    for (const c of cats) {
      const sRaw = scores[c.key].trim();
      const old = isObj(cont[c.key]) ? (cont[c.key] as Obj) : {};
      const entry: Obj = { ...old, evidence: evidence[c.key] };
      if (sRaw) {
        const n = Number(sRaw);
        if (!Number.isFinite(n) || !/^\d+(\.\d)?$/.test(sRaw) || n < c.min || n > c.max) { setLocalErr(`${c.label}: score must be ${c.min}–${c.max} with at most one decimal place.`); return null; }
        entry.score = n.toFixed(1);
      } else entry.score = null;
      cont[c.key] = entry;
    }
    if (nested) next.categories = cont; else Object.assign(next, cont);
    if (count.trim()) {
      const n = Number(count);
      if (!Number.isInteger(n) || n < 1 || n > 10000) { setLocalErr('Declared observations must be a whole number from 1 to 10000.'); return null; }
      next.count = n;
    } else next.count = null;
    if (context.trim() || 'context' in payload) next.context = context;
    if (notes.trim() || 'notes' in payload) next.notes = notes;
    setLocalErr(null);
    return next;
  };

  const dirty = useMemo(() => {
    const keys = cats.map((c) => c.key);
    for (const k of keys) {
      const o = isObj(container[k]) ? (container[k] as Obj) : {};
      if (String(o.score ?? '') !== scores[k].trim() || String(o.evidence ?? '') !== evidence[k]) return true;
    }
    return String(payload.count ?? '') !== count.trim() || String(payload.context ?? '') !== context || String(payload.notes ?? '') !== notes;
  }, [cats, container, payload, scores, evidence, count, context, notes]);

  const valuesFor = (op: ClubOperation | undefined, extra: Obj) => {
    if (!op) return { draft: id, revision, ...extra };
    const out: Obj = {};
    for (const f of op.fields) {
      if (f.name === 'payload') out.payload = extra.payload !== undefined ? extra.payload : JSON.stringify(payload);
      else if (/rev/i.test(f.name)) out[f.name] = revision;
      else if (/^(draft|id|draft_?id)$/i.test(f.name)) out[f.name] = id;
    }
    return out;
  };

  const correct = () => {
    const next = assemble();
    if (!next) return;
    const op = find('correct');
    onRun(op?.id ?? 'draft.correct', valuesFor(op, { payload: JSON.stringify(next) }), false, 'correct');
  };
  const save = () => { const op = find('save'); onRun(op?.id ?? 'draft.save', valuesFor(op, {}), true, 'save'); };
  const discard = () => { const op = find('discard'); onRun(op?.id ?? 'draft.discard', valuesFor(op, {}), true, 'discard'); };

  const filled = cats.filter((c) => scores[c.key].trim() && evidence[c.key].trim()).length;
  const meta = Object.entries(draft).filter(([k, v]) => !['payload', 'categories', 'id', 'revision', 'position'].includes(k) && v != null && typeof v !== 'object');

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !pending && onClose()}>
      <DialogContent className="max-h-[94dvh] overflow-y-auto sm:max-w-3xl" data-testid="dialog-evaluation-editor">
        <DialogHeader>
          <DialogTitle className="font-display flex flex-wrap items-center gap-2">
            Evaluation draft {draft.position != null && <Badge variant="secondary">{String(draft.position)}</Badge>}
            <Badge variant="outline" className="font-mono">rev {String(revision ?? '?')}</Badge>
          </DialogTitle>
          <DialogDescription>
            Score each category with evidence. Corrections update this draft only. Nothing is final until you save it with explicit confirmation.
          </DialogDescription>
        </DialogHeader>
        {meta.length > 0 && (
          <dl className="flex flex-wrap gap-x-5 gap-y-1 rounded-md bg-muted/60 px-3 py-2 text-xs">
            {meta.slice(0, 6).map(([k, v]) => (<div key={k}><dt className="inline text-muted-foreground">{humanize(k)}: </dt><dd className="inline font-medium"><Inline text={String(v)} /></dd></div>))}
          </dl>
        )}
        {cats.length === 0 && <p className="rounded-md border border-destructive/40 p-3 text-sm">This draft did not include its category definitions, so it cannot be edited here.</p>}
        <div className="space-y-3">
          {cats.map((c) => (
            <fieldset key={c.key} className="rounded-lg border bg-card p-3" data-testid={`category-${c.key}`}>
              <div className="flex items-start justify-between gap-3">
                <div>
                  <legend className="font-display text-sm font-bold">{c.label}</legend>
                  {c.guidance && <p className="mt-0.5 text-xs text-muted-foreground">{c.guidance}</p>}
                </div>
                <div className="w-24 shrink-0">
                  <Label htmlFor={`s-${c.key}`} className="text-[10px] uppercase tracking-wider text-muted-foreground">Score {c.min}-{c.max}</Label>
                  <Input id={`s-${c.key}`} type="number" min={c.min} max={c.max} step={0.1} value={scores[c.key]} onChange={(e) => setScores((p) => ({ ...p, [c.key]: e.target.value }))} className="h-8 text-center font-mono" data-testid={`input-score-${c.key}`} />
                </div>
              </div>
              <Textarea rows={2} maxLength={500} placeholder="Evidence behind this score" aria-label={`${c.label} observed evidence`} value={evidence[c.key]} onChange={(e) => setEvidence((p) => ({ ...p, [c.key]: e.target.value }))} className="mt-2 text-sm" data-testid={`input-evidence-${c.key}`} />
            </fieldset>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-[8rem_1fr]">
          <div><Label htmlFor="ev-count">Declared observations</Label><Input id="ev-count" type="number" min={1} max={10000} step={1} value={count} onChange={(e) => setCount(e.target.value)} data-testid="input-count" /></div>
          <div><Label htmlFor="ev-context">Context</Label><Input id="ev-context" maxLength={500} value={context} onChange={(e) => setContext(e.target.value)} data-testid="input-context" /></div>
        </div>
        <div><Label htmlFor="ev-notes">Notes</Label><Textarea id="ev-notes" rows={3} maxLength={1000} value={notes} onChange={(e) => setNotes(e.target.value)} data-testid="input-notes" /></div>
        {draft.result != null && isObj(draft.result) && <p className="rounded-md bg-muted p-3 text-sm">Current weighted human rating: {String(draft.result.score)}. This is staff evidence, not a prediction or automatic promotion.</p>}

        {(localErr || !!error) && (
          <div className="flex gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm" role="alert" data-testid="text-editor-error">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <div>
              <p className="font-medium">{localErr ?? errorText(error)}</p>
              {!localErr && <p className="text-muted-foreground">If this may have been interrupted, read the draft again and check existing records before repeating it. Nothing is retried automatically.</p>}
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3">
          <p className="text-xs text-muted-foreground">{filled} of {cats.length} categories scored with evidence{dirty ? '. Unapplied edits.' : '.'}</p>
          <Button variant="secondary" onClick={correct} disabled={pending || !dirty} data-testid="button-apply-corrections">
            {pending && pendingKind === 'correct' ? <Loader2 className="animate-spin" /> : <PencilLine />} Apply corrections
          </Button>
        </div>

        <div className="grid gap-3 rounded-lg border border-accent/50 bg-accent/10 p-3 sm:grid-cols-2">
          <div className="space-y-2">
            <label className="flex items-start gap-2 text-sm">
              <Checkbox checked={confirmSave} onCheckedChange={(c) => setConfirmSave(c === true)} className="mt-0.5" data-testid="check-confirm-save" />
              <span>Save revision {String(revision)} as the final evaluation.</span>
            </label>
            <Button onClick={save} disabled={pending || !confirmSave || dirty} className="w-full" data-testid="button-save-draft">
              {pending && pendingKind === 'save' ? <Loader2 className="animate-spin" /> : <Save />} Save evaluation
            </Button>
            {dirty && <p className="text-xs text-muted-foreground">Apply corrections first so the saved revision matches what you see.</p>}
          </div>
          <div className="space-y-2">
            <label className="flex items-start gap-2 text-sm">
              <Checkbox checked={confirmDiscard} onCheckedChange={(c) => setConfirmDiscard(c === true)} className="mt-0.5" data-testid="check-confirm-discard" />
              <span>Discard this draft permanently.</span>
            </label>
            <Button variant="destructive" onClick={discard} disabled={pending || !confirmDiscard} className="w-full" data-testid="button-discard-draft">
              {pending && pendingKind === 'discard' ? <Loader2 className="animate-spin" /> : <Trash2 />} Discard draft
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
