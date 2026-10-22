import { useEffect, useState } from 'react';
import { Link, Redirect } from 'wouter';
import { useAuth, useClerk } from '@clerk/react';
import { useQueryClient } from '@tanstack/react-query';
import {
  getGetPlayerAccessQueryKey,
  getGetPlayerDevelopmentQueryKey,
  useGetPlayerAccess,
  useGetPlayerDevelopment,
} from '@workspace/api-client-react';
import { AlertTriangle, ChevronLeft, ChevronRight, CheckCircle2, Clock, LogOut, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { LinkFlow } from '@/components/admin/link-flow';
import { emblem } from '@/components/public-layout';
import { errorStatus, errorText } from '@/lib/format';

const POLL_MS = 60_000;
const MAX_POLLS = 10;

function Notice({ title, body, onRetry, testid }: { title: string; body: string; onRetry?: () => void; testid: string }) {
  return (
    <div className="mx-auto max-w-lg rounded-xl border border-border bg-card p-8 text-center" role="status" data-testid={testid}>
      <AlertTriangle className="mx-auto h-8 w-8 text-[hsl(38_70%_38%)]" />
      <h2 className="mt-3 font-display text-xl font-bold">{title}</h2>
      <p className="mt-2 text-sm text-muted-foreground">{body}</p>
      {onRetry && <Button className="mt-4" variant="outline" onClick={onRetry} data-testid={`${testid}-retry`}><RefreshCw /> Try again</Button>}
    </div>
  );
}

function formatDue(iso: string, tz: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'Deadline unavailable';
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeStyle: 'short', timeZone: tz }).format(d) + ` (${tz})`;
  } catch {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'full', timeStyle: 'short' }).format(d);
  }
}

function Goals({ guild, enabled }: { guild: string; enabled: boolean }) {
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [guild]);
  const params = { guild, page };
  const q = useGetPlayerDevelopment(params, {
    query: {
      queryKey: getGetPlayerDevelopmentQueryKey(params),
      enabled,
      retry: false,
      refetchOnWindowFocus: true,
      refetchInterval: (query) => (query.state.dataUpdateCount < MAX_POLLS ? POLL_MS : false),
    },
  });
  const failed = q.isError;
  if (q.isFetching || q.isLoading || !enabled) {
    return <div className="space-y-3" data-testid="loading-goals">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full" />)}</div>;
  }
  if (failed || !q.data) {
    const st = errorStatus(q.error);
    return <Notice testid="error-goals" title="Goals unavailable"
      body={st === 403 ? 'Discord no longer confirms you as a member of this server. Nothing is shown.' : errorText(q.error)}
      onRetry={() => void q.refetch()} />;
  }
  const { goals, hasNext } = q.data;
  return (
    <div data-testid="panel-goals">
      {goals.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border bg-card p-10 text-center" data-testid="empty-goals">
          <p className="font-display text-lg font-bold">{page > 1 ? 'No goals on this page' : 'No shared goals yet'}</p>
          <p className="mt-2 text-sm text-muted-foreground">Staff choose which goals to share with you. When one is shared, it appears here with its deadline.</p>
        </div>
      ) : (
        <ul className="space-y-3">
          {goals.map((g) => {
            const done = g.status === 'completed';
            return (
              <li key={g.id} className="rounded-xl border border-border bg-card p-5" data-testid={`card-goal-${g.id}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className={`inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-widest ${done ? 'text-[hsl(150_45%_28%)]' : 'text-[hsl(38_70%_34%)]'}`} data-testid={`status-goal-${g.id}`}>
                    {done ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Clock className="h-3.5 w-3.5" />}{done ? 'Completed' : 'Active'}
                  </span>
                </div>
                <p className="mt-2 whitespace-pre-wrap break-words text-base" data-testid={`text-goal-${g.id}`}>{g.text}</p>
                <p className="mt-3 text-sm text-muted-foreground" data-testid={`text-due-${g.id}`}>Due {formatDue(g.dueAt, g.timezone)}</p>
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-5 flex items-center justify-between">
        <Button variant="outline" size="sm" disabled={page <= 1 || q.isFetching} onClick={() => setPage((p) => Math.max(1, p - 1))} data-testid="button-prev-page"><ChevronLeft /> Previous</Button>
        <span className="font-mono text-xs text-muted-foreground" data-testid="text-page">Page {q.data.page}</span>
        <Button variant="outline" size="sm" disabled={!hasNext || q.isFetching} onClick={() => setPage((p) => p + 1)} data-testid="button-next-page">Next <ChevronRight /></Button>
      </div>
      <div className="mt-4 text-right">
        <Button variant="ghost" size="sm" onClick={() => void q.refetch()} disabled={q.isFetching} data-testid="button-refresh-goals">
          <RefreshCw className={q.isFetching ? 'animate-spin' : ''} /> Refresh goals
        </Button>
      </div>
    </div>
  );
}

function Body() {
  const qc = useQueryClient();
  const [guild, setGuild] = useState('');
  const access = useGetPlayerAccess({
    query: {
      queryKey: getGetPlayerAccessQueryKey(),
      retry: false,
      refetchOnWindowFocus: true,
      refetchInterval: (query) => (query.state.dataUpdateCount < MAX_POLLS ? POLL_MS : false),
    },
  });
  const data = access.isError ? undefined : access.data;
  const servers = data?.servers ?? [];
  const linked = !!data?.linked;
  const authorized = linked && servers.length > 0;
  const ids = servers.map((s) => s.id).join(',');
  const validGuild = servers.some((s) => s.id === guild) ? guild : '';

  useEffect(() => {
    if (!authorized) {
      setGuild('');
      void qc.cancelQueries({ queryKey: getGetPlayerDevelopmentQueryKey() });
      qc.removeQueries({ queryKey: getGetPlayerDevelopmentQueryKey() });
      return;
    }
    setGuild((g) => (servers.some((s) => s.id === g) ? g : servers[0].id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authorized, ids, qc]);

  if (access.isLoading) return <div className="space-y-3" data-testid="loading-access"><Skeleton className="h-12 w-64" /><Skeleton className="h-24 w-full" /></div>;
  if (access.isError || !data) {
    return <Notice testid="error-access" title="Could not verify your access"
      body={errorStatus(access.error) === 401 ? 'Your session was not accepted. Sign in again.' : `${errorText(access.error)} Access fails closed, so nothing is shown until it recovers.`}
      onRetry={() => void access.refetch()} />;
  }
  if (!linked) return <LinkFlow audience="player" onRefresh={() => void access.refetch()} refreshing={access.isFetching} />;
  if (servers.length === 0) {
    return <Notice testid="empty-servers" title="No servers found"
      body="Your Discord account is verified, but you are not a current member of any server where Club Agent is installed."
      onRetry={() => void access.refetch()} />;
  }
  return (
    <div>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="font-mono text-xs uppercase tracking-[0.3em] text-[hsl(38_70%_34%)]">{data.discordName ? `Verified as ${data.discordName}` : 'Verified Discord account'}</p>
          <h1 className="mt-1 font-display text-3xl font-extrabold md:text-4xl">My development</h1>
          <p className="mt-1 text-sm text-muted-foreground">Goals your staff have shared with you. Read only.</p>
        </div>
        <div className="w-full sm:w-64">
          <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.2em] text-muted-foreground">Server</p>
          <Select value={validGuild} onValueChange={setGuild}>
            <SelectTrigger data-testid="select-server"><SelectValue placeholder="Choose a server" /></SelectTrigger>
            <SelectContent>
              {servers.map((s) => <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      </div>
      {validGuild ? <Goals key={validGuild} guild={validGuild} enabled={authorized && !access.isFetching} /> : null}
      <div className="mt-6 text-right">
        <Button variant="ghost" size="sm" onClick={() => void access.refetch()} disabled={access.isFetching} data-testid="button-refresh-access-player">
          <RefreshCw className={access.isFetching ? 'animate-spin' : ''} /> Recheck membership
        </Button>
      </div>
    </div>
  );
}

export default function DevelopmentPage() {
  const { isLoaded, isSignedIn } = useAuth();
  const { signOut } = useClerk();
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  if (!isLoaded) return <div className="grid min-h-[60dvh] place-items-center" role="status">Loading…</div>;
  if (!isSignedIn) return <Redirect to="/sign-in?next=/development" />;
  return (
    <div className="grain min-h-[100dvh] bg-background">
      <header className="border-b border-[hsl(var(--sidebar-border))] bg-ink text-[hsl(40_30%_90%)]">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-4">
          <Link href="/" className="flex items-center gap-2.5" data-testid="link-brand">
            <img src={emblem} alt="" className="h-8 w-8 rounded-lg object-cover ring-1 ring-accent/60" />
            <span className="font-display text-sm font-bold tracking-[0.18em]">CLUB AGENT</span>
          </Link>
          <button className="flex items-center gap-2 text-sm" onClick={() => signOut({ redirectUrl: base || '/' })} data-testid="button-sign-out">
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-8">
        <Body />
      </main>
    </div>
  );
}
