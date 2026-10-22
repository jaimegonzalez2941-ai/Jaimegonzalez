import { type ReactNode, useState } from 'react';
import { Link, useLocation } from 'wouter';
import { useAuth } from '@clerk/react';
import { Menu, X, ArrowUpRight, ExternalLink } from 'lucide-react';
import { useGetClubPublic } from '@workspace/api-client-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { trackEvent, type InstallLocation } from '@/lib/analytics';

const base = import.meta.env.BASE_URL.replace(/\/$/, '');
export const emblem = `${base}/club-agent.jpg`;

export function InstallCta({ location, className, tone = 'dark' }: { location: InstallLocation; className?: string; tone?: 'dark' | 'light' }) {
  const { data, isLoading, isError, refetch } = useGetClubPublic();
  if (isLoading) return <Skeleton className="h-11 w-56" />;
  if (isError) {
    return (
      <div className={className} data-testid="install-unavailable">
        <p className="text-sm opacity-80">The install service could not be reached, so no install link is shown.</p>
        <button className="mt-1 text-sm font-medium underline underline-offset-4" onClick={() => refetch()} data-testid="button-retry-install">Try again</button>
      </div>
    );
  }
  if (data?.installUrl) {
    return (
      <div className={className}>
        <Button asChild size="lg" className={tone === 'dark' ? 'bg-accent text-accent-foreground border-accent' : ''}>
          <a href={data.installUrl} target="_blank" rel="noopener noreferrer" data-testid="link-install"
            onClick={() => trackEvent({ name: 'install_link_clicked', location })}>
            Add Club Agent to Discord <ExternalLink />
          </a>
        </Button>
      </div>
    );
  }
  return (
    <div className={className} data-testid="install-unavailable">
      <p className="max-w-md text-sm opacity-85">
        {data?.message || 'An install link is not available from this deployment right now. Ask whoever runs this bot for an invite; none is made up here.'}
      </p>
    </div>
  );
}

export function PublicLayout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [loc] = useLocation();
  const { isSignedIn } = useAuth();
  const links = [
    { href: '/', label: 'Features' },
    { href: '/guide', label: 'Start here' },
    { href: '/development', label: 'My development' },
  ];
  return (
    <div className="grain min-h-[100dvh] bg-background">
      <header className="sticky top-0 z-40 border-b border-[hsl(var(--sidebar-border))] bg-ink/95 text-[hsl(40_30%_90%)] backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
          <Link href="/" className="flex items-center gap-2.5" data-testid="link-home">
            <img src={emblem} alt="" className="h-8 w-8 rounded-lg object-cover ring-1 ring-accent/60" />
            <span className="font-display text-sm font-bold tracking-[0.18em]">CLUB AGENT</span>
          </Link>
          <nav className="hidden items-center gap-1 md:flex">
            {links.map((l) => (
              <Link key={l.href} href={l.href} data-testid={`link-nav-${l.label}`}
                className={`rounded-md px-3 py-1.5 text-sm transition-colors hover:text-accent ${loc === l.href ? 'text-accent' : ''}`}>
                {l.label}
              </Link>
            ))}
            <Button asChild size="sm" className="ml-2 bg-accent text-accent-foreground border-accent">
              <Link href={isSignedIn ? '/admin' : '/sign-in'} data-testid="link-workspace">
                {isSignedIn ? 'Open workspace' : 'Staff sign in'} <ArrowUpRight />
              </Link>
            </Button>
          </nav>
          <button className="md:hidden" onClick={() => setOpen((o) => !o)} aria-label="Menu" data-testid="button-menu">
            {open ? <X /> : <Menu />}
          </button>
        </div>
        {open && (
          <div className="flex flex-col gap-1 border-t border-[hsl(var(--sidebar-border))] px-4 py-3 md:hidden">
            {links.map((l) => <Link key={l.href} href={l.href} onClick={() => setOpen(false)} className="py-2">{l.label}</Link>)}
            <Link href={isSignedIn ? '/admin' : '/sign-in'} onClick={() => setOpen(false)} className="py-2 font-medium text-accent">
              {isSignedIn ? 'Open workspace' : 'Staff sign in'}
            </Link>
          </div>
        )}
      </header>
      {children}
      <footer className="bg-ink text-[hsl(40_30%_80%)]">
        <div className="hieroglyph-rule" />
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-10 md:grid-cols-[1fr_auto]">
          <div>
            <p className="font-display text-lg font-bold tracking-[0.18em] text-accent">CLUB AGENT</p>
            <p className="mt-2 max-w-md text-sm opacity-80">A private staff companion for FC Pro Clubs. Records you can trace, operations you can account for. Access comes only from a linked Discord account with current staff roles.</p>
          </div>
          <div className="flex gap-6 text-sm">
            <Link href="/guide" className="hover:text-accent">Start here</Link>
            <Link href="/development" className="hover:text-accent" data-testid="link-footer-development">My development</Link>
            <Link href="/sign-in" className="hover:text-accent">Sign in</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
