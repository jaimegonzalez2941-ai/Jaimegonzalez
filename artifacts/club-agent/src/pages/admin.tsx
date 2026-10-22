import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, Redirect, useParams } from 'wouter';
import { useAuth, useClerk } from '@clerk/react';
import { useQueryClient, keepPreviousData } from '@tanstack/react-query';
import {
  getGetClubAccessQueryKey, getGetClubWorkspaceQueryKey, useGetClubAccess, useGetClubWorkspace, useRunClubAction,
  type ActionResult, type ClubOperation, type ClubServer, type GetClubWorkspaceParams,
} from '@workspace/api-client-react';
import {
  AlertTriangle, ChevronLeft, ChevronRight, LogOut, Menu, RefreshCw, Search, X, Inbox, ScrollText, Shield, Radio, ArrowLeft,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { emblem } from '@/components/public-layout';
import { LinkFlow } from '@/components/admin/link-flow';
import { OperationDialog } from '@/components/admin/operation-dialog';
import { OperationsPanel } from '@/components/admin/operations-panel';
import { EvaluationEditor } from '@/components/admin/evaluation-editor';
import { EvidenceBlocks, RecordCard } from '@/components/admin/records';
import { SCREENS, SCREEN_KEYS } from '@/lib/screens';
import { Markdown, errorStatus, errorText } from '@/lib/format';
import { cn } from '@/lib/utils';

const GUILD_KEY = 'club-agent.guild';

function ServiceDown({ title, error, onRetry }: { title: string; error: unknown; onRetry: () => void }) {
  const st = errorStatus(error);
  return (
    <div className="mx-auto max-w-lg py-16 text-center" data-testid="panel-service-down">
      <AlertTriangle className="mx-auto h-9 w-9 text-destructive" />
      <h2 className="mt-3 font-display text-xl font-bold">{title}</h2>
      <p className="mt-2 text-sm text-muted-foreground">
        {st === 401 ? 'Your session was not accepted. Sign in again.' : st === 403 ? 'Discord could not confirm your current staff access for this server.' : st && st >= 500 ? 'The service or its connection to Discord is not available. Access fails closed, so nothing is shown until it recovers.' : errorText(error)}
      </p>
      <Button className="mt-4" variant="outline" onClick={onRetry} data-testid="button-retry"><RefreshCw /> Try again</Button>
    </div>
  );
}

function SignOutButton({ className }: { className?: string }) {
  const { signOut } = useClerk();
  const base = import.meta.env.BASE_URL.replace(/\/$/, '');
  return (
    <button className={cn('flex items-center gap-2 text-sm', className)} onClick={() => signOut({ redirectUrl: base || '/' })} data-testid="button-sign-out">
      <LogOut className="h-4 w-4" /> Sign out
    </button>
  );
}

function Shell({ servers, guild, setGuild, screen, discordName, children }: {
  servers: ClubServer[]; guild: string; setGuild: (g: string) => void; screen?: string; discordName?: string; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const current = servers.find((s) => s.id === guild);
  const nav = (
    <nav className="flex flex-col gap-0.5" aria-label="Workspace">
      {SCREENS.map((s) => (
        <Link key={s.key} href={`/admin/${s.key}`} onClick={() => setOpen(false)} data-testid={`link-screen-${s.key}`}
          className={cn('flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors',
            screen === s.key ? 'bg-sidebar-accent font-semibold text-sidebar-primary' : 'text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground')}>
          <s.icon className="h-4 w-4" />{s.label}
          {screen === s.key && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-sidebar-primary" />}
        </Link>
      ))}
    </nav>
  );
  const side = (
    <div className="flex h-full flex-col gap-4 p-3">
      <Link href="/" className="flex items-center gap-2.5 px-1 pt-1" data-testid="link-brand">
        <img src={emblem} alt="" className="h-9 w-9 rounded-lg object-cover ring-1 ring-sidebar-primary/60" />
        <span className="font-display text-sm font-bold tracking-[0.18em] text-sidebar-foreground">CLUB AGENT</span>
      </Link>
      {servers.length > 0 && (
        <div>
          <p className="mb-1 px-1 font-mono text-[10px] uppercase tracking-[0.2em] text-sidebar-foreground/50">Server</p>
          <Select value={guild} onValueChange={setGuild}>
            <SelectTrigger className="h-9 border-sidebar-border bg-sidebar-accent text-sidebar-foreground" data-testid="select-server"><SelectValue placeholder="Choose a server" /></SelectTrigger>
            <SelectContent>
              {servers.map((s) => <SelectItem key={s.id} value={s.id}>{s.name}{s.manager ? ' (manager)' : ''}</SelectItem>)}
            </SelectContent>
          </Select>
          {current && <p className="mt-1.5 flex items-center gap-1 px-1 text-[11px] text-sidebar-foreground/60"><Shield className="h-3 w-3" />{current.manager ? 'Manager access' : 'Staff access'}</p>}
        </div>
      )}
      {servers.length > 0 && nav}
      <div className="mt-auto space-y-2 border-t border-sidebar-border px-1 pt-3 text-sidebar-foreground/80">
        {discordName && <p className="truncate text-xs" data-testid="text-discord-name">Linked as <span className="font-semibold text-sidebar-primary">{discordName}</span></p>}
        <SignOutButton className="hover:text-sidebar-primary" />
      </div>
    </div>
  );
  return (
    <div className="grain min-h-[100dvh] bg-background md:grid md:grid-cols-[232px_1fr]">
      <aside className="sticky top-0 hidden h-[100dvh] bg-sidebar md:block">{side}</aside>
      <header className="sticky top-0 z-30 flex h-12 items-center justify-between bg-sidebar px-3 text-sidebar-foreground md:hidden">
        <Link href="/" className="font-display text-sm font-bold tracking-[0.18em]">CLUB AGENT</Link>
        <button onClick={() => setOpen(true)} aria-label="Open navigation" data-testid="button-open-nav"><Menu /></button>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 md:hidden">
          <div className="absolute inset-0 bg-ink/60" onClick={() => setOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-72 animate-in slide-in-from-left bg-sidebar duration-200">
            <button className="absolute right-3 top-3 text-sidebar-foreground" onClick={() => setOpen(false)} aria-label="Close navigation"><X /></button>
            {side}
          </div>
        </div>
      )}
      <main className="min-w-0">{children}</main>
    </div>
  );
}

export default function AdminPage() {
  const { isLoaded, isSignedIn } = useAuth();
  if (!isLoaded) return <div className="p-8"><Skeleton className="h-10 w-64" /></div>;
  if (!isSignedIn) return <Redirect to="/sign-in" />;
  return <Gate />;
}

function Gate() {
  const params = useParams<{ screen?: string }>();
  const screen = params.screen && SCREEN_KEYS.includes(params.screen) ? params.screen : 'overview';
  const qc = useQueryClient();
  const access = useGetClubAccess({ query: { queryKey: getGetClubAccessQueryKey(), retry: false } });
  const servers = useMemo(() => access.data?.servers ?? [], [access.data]);
  const [guild, setGuildState] = useState<string>(() => localStorage.getItem(GUILD_KEY) ?? '');
  const valid = servers.find((s) => s.id === guild) ?? servers[0];
  const setGuild = (g: string) => { localStorage.setItem(GUILD_KEY, g); setGuildState(g); };

  const refresh = () => qc.invalidateQueries({ queryKey: getGetClubAccessQueryKey() });

  if (access.isLoading) {
    return <Shell servers={[]} guild="" setGuild={setGuild} screen={screen}><div className="space-y-3 p-8"><Skeleton className="h-9 w-72" /><Skeleton className="h-40 w-full" /><Skeleton className="h-40 w-full" /></div></Shell>;
  }
  if (access.isError) {
    return <Shell servers={[]} guild="" setGuild={setGuild}><ServiceDown title="Access service unavailable" error={access.error} onRetry={() => access.refetch()} /></Shell>;
  }
  if (!access.data?.linked) {
    return <Shell servers={[]} guild="" setGuild={setGuild}><LinkFlow onRefresh={() => access.refetch()} refreshing={access.isFetching} /></Shell>;
  }
  if (!valid) {
    return (
      <Shell servers={[]} guild="" setGuild={setGuild} discordName={access.data.discordName}>
        <div className="mx-auto max-w-lg py-20 text-center" data-testid="panel-no-servers">
          <Inbox className="mx-auto h-9 w-9 text-muted-foreground" />
          <h2 className="mt-3 font-display text-xl font-bold">No servers available to you</h2>
          <p className="mt-2 text-sm text-muted-foreground">Your Discord account is linked, but Discord did not confirm you as current staff in any server where Club Agent is installed. Ask a manager to check your staff role, then refresh.</p>
          <Button className="mt-4" variant="outline" onClick={() => access.refetch()} disabled={access.isFetching}><RefreshCw className={access.isFetching ? 'animate-spin' : ''} /> Refresh access</Button>
        </div>
      </Shell>
    );
  }
  return (
    <Shell servers={servers} guild={valid.id} setGuild={setGuild} screen={screen} discordName={access.data.discordName}>
      <Workspace key={`${valid.id}:${screen}`} guild={valid.id} screen={screen} onAuthLost={refresh} />
    </Shell>
  );
}

type Obj = Record<string, unknown>;

function Workspace({ guild, screen, onAuthLost }: { guild: string; screen: string; onAuthLost: () => void }) {
  const qc = useQueryClient();
  const { toast } = useToast();
  const def = SCREENS.find((s) => s.key === screen)!;
  const [page, setPage] = useState(1);
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [snap, setSnap] = useState<string | undefined>();
  const [evidence, setEvidence] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const [op, setOp] = useState<ClubOperation | null>(null);
  const [result, setResult] = useState<{ label: string; r: ActionResult } | null>(null);
  const [draft, setDraft] = useState<Obj | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [pendingKind, setPendingKind] = useState<string | null>(null);
  const draftOps = useRef<ClubOperation[]>([]);

  const params: GetClubWorkspaceParams = useMemo(() => ({
    guild,
    screen: evidence ? 'snapshot' : screen,
    page,
    ...(!evidence && search ? { search } : {}),
    ...(!evidence && status ? { status } : {}),
    ...(snap ? { snapshot: snap } : {}),
  }), [guild, screen, page, search, status, snap, evidence]);

  const ws = useGetClubWorkspace(params, {
    query: { queryKey: getGetClubWorkspaceQueryKey(params), enabled: !!guild, placeholderData: keepPreviousData, retry: false },
  });
  const data = ws.data;

  useEffect(() => { if ([401, 403].includes(errorStatus(ws.error) ?? 0)) onAuthLost(); }, [ws.error, onAuthLost]);
  useEffect(() => {
    if (data?.operations?.some((o) => /draft/i.test(o.id))) draftOps.current = data.operations.filter((o) => /draft/i.test(o.id));
  }, [data]);
  useEffect(() => () => clearTimeout(timer.current), []);

  const action = useRunClubAction();

  const onSearch = (v: string) => {
    setSearchInput(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => { setSearch(v.trim()); setPage(1); setSnap(undefined); }, 400);
  };
  const refreshAll = () => {
    setSnap(undefined); setEvidence(false); setPage(1);
    qc.invalidateQueries({ queryKey: getGetClubWorkspaceQueryKey() });
  };
  const next = () => { if (data?.snapshot) setSnap(data.snapshot); setPage((p) => p + 1); };

  const run = (operation: string, values: Obj, confirmed: boolean, label: string, kind: string) => {
    setPendingKind(kind);
    action.mutate({ data: { guild, operation, values, confirmed } }, {
      onSuccess: (r) => {
        const isEnd = /draft/i.test(operation) && /(save|discard)$/i.test(operation);
        if (isEnd) { setDraft(null); setEditorOpen(false); }
        else if (r.draft) { setDraft(r.draft); setEditorOpen(true); }
        setOp(null);
        if (!(r.draft && !isEnd) || r.blocks?.length) setResult({ label, r });
        toast({ title: label, description: r.message?.slice(0, 140) });
        if (!evidence) setSnap(undefined);
        qc.invalidateQueries({ queryKey: getGetClubWorkspaceQueryKey() });
      },
    });
  };

  const openOp = (o: ClubOperation) => {
    action.reset();
    if (/draft/i.test(o.id) && /correct$/i.test(o.id) && draft) { setEditorOpen(true); return; }
    setOp(o);
  };
  const live = data?.live ?? '';
  const isLive = /live|connected|online|ok/i.test(live) && !/not|offline|unavailable|saved/i.test(live);
  const rows = data?.rows ?? [];
  const showToolbar = !evidence && ['schedule', 'availability', 'attendance', 'scouting', 'evaluations', 'recruitment', 'goals', 'tasks', 'audit'].includes(screen);
  const searchPlaceholder = screen === 'schedule' ? 'Search session titles'
    : screen === 'scouting' ? 'Saved player name or Discord ID'
    : screen === 'tasks' ? 'Staff owner ID or task words'
    : screen === 'audit' ? 'Search audit action names' : 'Exact player Discord ID';

  return (
    <div className="mx-auto max-w-[1400px] p-3 md:p-6">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[11px] uppercase tracking-[0.25em] text-[hsl(38_70%_34%)]">{evidence ? 'Evidence' : def.label}</p>
          <h1 className="font-display text-2xl font-extrabold md:text-3xl" data-testid="text-screen-title">{data?.title ?? def.label}</h1>
          <p className="text-sm text-muted-foreground">{def.blurb}</p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {live && (
            <p className="inline-flex max-w-full items-start gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-semibold leading-relaxed [overflow-wrap:anywhere]" data-testid="badge-live">
              <Radio className={cn('mt-0.5 h-3 w-3 shrink-0', isLive ? 'text-emerald-600' : 'text-amber-600')} /><span className="min-w-0">{live}</span>
            </p>
          )}
          <Button size="sm" variant="outline" onClick={refreshAll} disabled={ws.isFetching} data-testid="button-refresh"><RefreshCw className={ws.isFetching ? 'animate-spin' : ''} /> Refresh</Button>
        </div>
      </div>

      {evidence && (
        <Button size="sm" variant="ghost" className="mb-3" onClick={refreshAll} data-testid="button-exit-evidence"><ArrowLeft /> Back to {def.label}</Button>
      )}

      {showToolbar && (
        <div className="mb-4 flex flex-wrap gap-2">
          <div className="relative min-w-[200px] flex-1 md:max-w-sm">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input value={searchInput} onChange={(e) => onSearch(e.target.value)} maxLength={100} placeholder={searchPlaceholder} aria-label={searchPlaceholder} className="pl-8" data-testid="input-search" />
          </div>
          {['schedule', 'availability', 'recruitment', 'goals', 'tasks'].includes(screen) && <div className="w-44">
            <select aria-label="Status filter" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); setSnap(undefined); }} className="h-9 w-full rounded-md border bg-card px-3 text-sm" data-testid="input-status">
              <option value="">Default view</option>
              {(screen === 'schedule' ? ['upcoming', 'past'] : screen === 'availability' ? ['pending', 'approved', 'declined', 'cancelled', 'all'] : screen === 'recruitment' ? ['active', 'overdue', 'archived', 'all'] : ['active', 'overdue', 'completed', 'all']).map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}
            </select>
          </div>}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          {ws.isLoading && !data && (
            <div className="space-y-3" data-testid="skeleton-workspace">
              <Skeleton className="shimmer h-24 w-full" /><div className="grid gap-3 md:grid-cols-2"><Skeleton className="shimmer h-36" /><Skeleton className="shimmer h-36" /></div>
            </div>
          )}
          {ws.isError && <ServiceDown title="This screen could not be loaded" error={ws.error} onRetry={() => ws.refetch()} />}
          {data && !ws.isError && (
            <div className={cn('space-y-4 transition-opacity', ws.isFetching && ws.isPlaceholderData && 'opacity-60')}>
              <EvidenceBlocks blocks={data.blocks} />
              {rows.length > 0 ? (
                <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3" data-testid="list-records">
                  {rows.map((r, i) => <RecordCard key={`${page}-${i}`} row={r} index={i} />)}
                </div>
              ) : !data.blocks?.length ? (
                <div className="rounded-xl border border-dashed p-10 text-center" data-testid="empty-records">
                  <Inbox className="mx-auto h-8 w-8 text-muted-foreground" />
                  <p className="mt-2 font-display font-bold">Nothing recorded here</p>
                  <p className="mt-1 text-sm text-muted-foreground">{search || status ? 'No records match this search and status.' : 'No records exist for this screen yet. Nothing is filled in for you.'}</p>
                </div>
              ) : null}
              {(page > 1 || data.hasNext) && (
                <div className="flex items-center justify-between rounded-lg border bg-card px-3 py-2">
                  <Button size="sm" variant="ghost" disabled={page <= 1 || ws.isFetching} onClick={() => setPage((p) => Math.max(1, p - 1))} data-testid="button-prev"><ChevronLeft /> Previous</Button>
                  <span className="font-mono text-xs text-muted-foreground" data-testid="text-page">Page {data.page ?? page}</span>
                  <Button size="sm" variant="ghost" disabled={!data.hasNext || ws.isFetching} onClick={next} data-testid="button-next">Next <ChevronRight /></Button>
                </div>
              )}
            </div>
          )}
        </div>

        {!evidence && (
          <aside className="space-y-3 xl:sticky xl:top-6 xl:self-start">
            <h2 className="flex items-center gap-2 font-display text-sm font-bold"><ScrollText className="h-4 w-4 text-[hsl(38_75%_38%)]" /> Operations</h2>
            {ws.isLoading && !data ? <Skeleton className="h-40" /> : (
              <OperationsPanel ops={ws.isError ? [] : data?.operations ?? []} onOpen={openOp} hasDraft={!!draft} />
            )}
            {draft && !editorOpen && (
              <Button className="w-full" variant="secondary" onClick={() => setEditorOpen(true)} data-testid="button-reopen-editor">Reopen evaluation draft</Button>
            )}
          </aside>
        )}
      </div>

      <OperationDialog
        op={op && !(/draft/i.test(op.id) && /correct$/i.test(op.id)) ? op : null}
        open={!!op && !(/draft/i.test(op.id) && /correct$/i.test(op.id))}
        onOpenChange={(o) => { if (!o) setOp(null); }}
        onSubmit={(values, confirmed) => op && run(op.id, values, confirmed, op.label, 'op')}
        pending={action.isPending && pendingKind === 'op'}
        error={pendingKind === 'op' ? action.error : null}
      />

      {draft && (
        <EvaluationEditor
          key={`${String(draft.id)}:${String(draft.revision)}`}
          draft={draft}
          ops={draftOps.current}
          open={editorOpen}
          onClose={() => setEditorOpen(false)}
          pending={action.isPending && pendingKind !== 'op'}
          pendingKind={pendingKind}
          error={pendingKind !== 'op' ? action.error : null}
          onRun={(operation, values, confirmed, kind) => { action.reset(); run(operation, values, confirmed, `Draft ${kind}`, kind); }}
        />
      )}

      <Dialog open={!!result} onOpenChange={(o) => !o && setResult(null)}>
        <DialogContent className="max-h-[88dvh] overflow-y-auto sm:max-w-2xl" data-testid="dialog-result">
          <DialogHeader>
            <DialogTitle className="font-display">{result?.label}</DialogTitle>
            <DialogDescription>Recorded. Workspace data has been refreshed.</DialogDescription>
          </DialogHeader>
          {result && (
            <div className="space-y-3">
              <div className="rounded-md bg-muted/70 p-3"><Markdown text={result.r.message} /></div>
              <EvidenceBlocks blocks={result.r.blocks ?? []} />
              {result.r.snapshot && (
                <Button onClick={() => { const s = result.r.snapshot; setResult(null); setSnap(s); setPage(1); setEvidence(true); }} data-testid="button-open-evidence">
                  Continue reading full evidence
                </Button>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
