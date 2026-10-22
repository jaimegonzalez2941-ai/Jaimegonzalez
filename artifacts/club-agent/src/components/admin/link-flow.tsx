import { useEffect, useState } from 'react';
import { useCreateClubLink } from '@workspace/api-client-react';
import { Copy, Check, Loader2, RefreshCw, AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { errorText } from '@/lib/format';

export function LinkFlow({ onRefresh, refreshing, audience = 'staff' }: { onRefresh: () => void; refreshing: boolean; audience?: 'staff' | 'player' }) {
  const player = audience === 'player';
  const create = useCreateClubLink();
  const code = create.data?.code;
  const [left, setLeft] = useState(0);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!create.data) return;
    setLeft(create.data.expiresIn);
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [create.data]);

  const cmd = code ? `/website code:${code}` : '';
  const copy = async () => {
    try { await navigator.clipboard.writeText(cmd); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard unavailable */ }
  };
  const mm = String(Math.floor(left / 60)).padStart(2, '0');
  const ss = String(left % 60).padStart(2, '0');

  return (
    <div className="mx-auto grid min-h-[70dvh] max-w-3xl content-center gap-6 px-4 py-10" data-testid="panel-link">
      <div>
        <p className="font-mono text-xs uppercase tracking-[0.3em] text-[hsl(38_70%_34%)]">Account not linked</p>
        <h1 className="mt-2 font-display text-3xl font-extrabold md:text-5xl">{player ? 'Prove this is you, from Discord.' : 'Prove you are staff, from Discord.'}</h1>
        <p className="mt-3 max-w-xl text-muted-foreground">{player ? 'Your website account is not tied to a Discord identity yet. Request' : 'Your website account has no club access yet. Request'} a one-time code here, then redeem it inside Discord with the account you use in your server. The website never takes a typed-in Discord identity.</p>
      </div>
      <ol className="space-y-3 text-sm">
        <li className="flex gap-3"><span className="font-mono text-accent-foreground/0 [-webkit-text-stroke:1px_hsl(38_70%_38%)] text-lg font-bold">1</span>Create a code.</li>
        <li className="flex gap-3"><span className="font-mono text-accent-foreground/0 [-webkit-text-stroke:1px_hsl(38_70%_38%)] text-lg font-bold">2</span>Run the command in a server where Club Agent is installed.</li>
        <li className="flex gap-3"><span className="font-mono text-accent-foreground/0 [-webkit-text-stroke:1px_hsl(38_70%_38%)] text-lg font-bold">3</span>Return here and refresh access.</li>
      </ol>
      {code ? (
        <div className="rounded-xl border-2 border-accent bg-ink p-5 text-[hsl(40_30%_92%)]" data-testid="panel-code">
          <p className="text-xs uppercase tracking-widest opacity-70">Run in Discord</p>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
            <code className="break-all font-mono text-lg font-medium text-accent md:text-2xl" data-testid="text-link-command">{cmd}</code>
            <Button size="sm" variant="secondary" onClick={copy} data-testid="button-copy-command">{copied ? <Check /> : <Copy />}{copied ? 'Copied' : 'Copy'}</Button>
          </div>
          <p className="mt-3 text-sm opacity-80" data-testid="text-expiry">{left > 0 ? `Expires in ${mm}:${ss}. Single use.` : 'This code has expired. Create a new one.'}</p>
        </div>
      ) : null}
      {create.isError && (
        <div className="flex gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm" role="alert" data-testid="text-link-error">
          <AlertTriangle className="mt-0.5 h-4 w-4 text-destructive" />
          <p>The link service is unavailable: {errorText(create.error)} No code was issued that you can rely on. Try again shortly.</p>
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        <Button onClick={() => create.mutate()} disabled={create.isPending} data-testid="button-create-link">
          {create.isPending && <Loader2 className="animate-spin" />}{code ? 'Create a new code' : 'Create link code'}
        </Button>
        <Button variant="outline" onClick={onRefresh} disabled={refreshing} data-testid="button-refresh-access">
          <RefreshCw className={refreshing ? 'animate-spin' : ''} /> I redeemed it, refresh access
        </Button>
      </div>
    </div>
  );
}
