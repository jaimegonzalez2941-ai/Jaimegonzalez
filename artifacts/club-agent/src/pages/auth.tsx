import { SignIn, SignUp } from '@clerk/react';
import { Link } from 'wouter';
import type { ReactNode } from 'react';
import { emblem } from '@/components/public-layout';

const basePath = import.meta.env.BASE_URL.replace(/\/$/, '');

function target(): string {
  const next = new URLSearchParams(window.location.search).get('next');
  return `${basePath}${next === '/development' ? '/development' : '/admin'}`;
}

function Frame({ children, line }: { children: ReactNode; line: string }) {
  return (
    <div className="grain grid min-h-[100dvh] md:grid-cols-[1fr_1fr]">
      <div className="relative hidden flex-col justify-between overflow-hidden bg-ink p-10 text-[hsl(40_30%_92%)] md:flex">
        <div className="pointer-events-none absolute -left-32 top-1/3 h-[480px] w-[480px] rounded-full bg-[hsl(214_100%_50%/0.18)] blur-3xl" />
        <Link href="/" className="relative font-display text-sm font-bold tracking-[0.2em]">CLUB AGENT</Link>
        <div className="relative">
          <img src={emblem} alt="" className="mb-8 w-56 rounded-3xl ring-1 ring-accent/50" />
          <p className="max-w-sm font-display text-3xl font-bold leading-tight">{line}</p>
        </div>
        <p className="relative max-w-sm text-xs opacity-70">Creating an account never grants access. Your verified Discord account and current membership determine player access. Staff workflows also require current staff permissions.</p>
      </div>
      <div className="flex items-center justify-center bg-background px-4 py-10">{children}</div>
    </div>
  );
}

export function SignInPage() {
  return <Frame line="Welcome back. The record is where you left it."><SignIn routing="path" path={`${basePath}/sign-in`} signUpUrl={`${basePath}/sign-up${window.location.search}`} forceRedirectUrl={target()} /></Frame>;
}
export function SignUpPage() {
  return <Frame line="Make an account. Then verify your own identity, from Discord."><SignUp routing="path" path={`${basePath}/sign-up`} signInUrl={`${basePath}/sign-in${window.location.search}`} forceRedirectUrl={target()} /></Frame>;
}
