import { useEffect } from 'react';
import { useLocation } from 'wouter';
import { SCREENS } from '@/lib/screens';

export function RouteMetadata() {
  const [location] = useLocation();
  useEffect(() => {
    const privatePage = /^\/(admin|sign-in|sign-up)(\/|$)/.test(location);
    const screen = SCREENS.find((s) => location === `/admin/${s.key}`);
    const title = location === '/' ? 'Club Agent — The record speaks. Staff decide.'
      : location === '/guide' ? 'Start here — Club Agent'
      : location.startsWith('/sign-in') ? 'Staff sign in — Club Agent'
      : location.startsWith('/sign-up') ? 'Create your account — Club Agent'
      : location.startsWith('/admin') ? `${screen?.label ?? 'Staff workspace'} — Club Agent`
      : 'Page not found — Club Agent';
    const description = location === '/guide'
      ? 'A workflow-first guide to club setup, scouting, recruitment, scheduling and attendance with Club Agent.'
      : privatePage ? 'Sign in and verify your own Discord account to access your club’s private staff workflows.'
      : 'Explainable Pro Clubs scouting, trusted match evidence and vacation-aware attendance. Private staff workflows alongside Discord.';
    document.title = title;
    const update = (selector: string, content: string) => document.querySelector(selector)?.setAttribute('content', content);
    update('meta[name="description"]', description);
    update('meta[property="og:title"]', title);
    update('meta[property="og:description"]', description);
    update('meta[name="twitter:title"]', title);
    update('meta[name="twitter:description"]', description);
    update('meta[name="robots"]', privatePage || location !== '/' && location !== '/guide' ? 'noindex, nofollow' : 'index, follow');
  }, [location]);
  return null;
}
