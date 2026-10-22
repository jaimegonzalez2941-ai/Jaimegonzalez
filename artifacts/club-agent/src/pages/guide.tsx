import { useEffect, useRef } from 'react';
import { Link } from 'wouter';
import { PublicLayout, InstallCta } from '@/components/public-layout';
import { Button } from '@/components/ui/button';
import { trackEvent } from '@/lib/analytics';

const steps: { t: string; body: string[]; code?: string[] }[] = [
  { t: 'Add the bot to your server', body: ['A server owner or manager adds Club Agent using the install link below. If no link is shown, this deployment has not published one.'] },
  { t: 'Configure the club in Discord', body: ['A manager runs setup, checks status, sets stage roles and the promotion standard. The default standard is 80% attendance over 30 days with 5 marked sessions, adjustable, and it never promotes anyone automatically.'], code: ['/scout setup', '/scout status', '/scout stage_role'] },
  { t: 'Launch the panel and docket from Discord', body: ['These are Discord workflows, not website publishing. The panel is manager-only. The docket is never republished automatically.'], code: ['/start_panel', '/scout docket'] },
  { t: 'Create your website account', body: ['Sign up or sign in. This proves who you are on the website only. It gives you no club access.'] },
  { t: 'Link your Discord account', body: ['Open the workspace and request a link code. Then redeem it inside Discord, from the server, as the account you play with. The website never asks you to type a Discord identity.'], code: ['/website code:YOURCODE'] },
  { t: 'Choose a server', body: ['Back in the workspace, refresh access. You will see only the servers where the bot can confirm you as current staff. Managers also see manager-only operations.'] },
  { t: 'Work from the record', body: ['Start with Overview, then Schedule and Availability. Mark attendance after sessions, add scouting recaps as trusted text, and save evaluations only after review.'] },
];

export default function Guide() {
  const entered = useRef(false);
  useEffect(() => {
    if (entered.current) return;
    entered.current = true;
    trackEvent({ name: 'start_here_entered', location: 'start_here_guide' });
  }, []);

  return (
    <PublicLayout>
      <section className="bg-ink text-[hsl(40_30%_92%)]">
        <div className="mx-auto max-w-4xl px-4 pb-14 pt-14">
          <p className="mb-3 font-mono text-xs uppercase tracking-[0.3em] text-accent">Start here</p>
          <h1 className="font-display text-4xl font-extrabold md:text-6xl">From install to first evaluation.</h1>
          <p className="mt-4 max-w-2xl opacity-85">This guide works without signing in. Seven steps, in order.</p>
        </div>
        <div className="hieroglyph-rule" />
      </section>
      <div className="mx-auto max-w-4xl px-4 py-14">
        <ol className="space-y-0">
          {steps.map((s, i) => (
            <li key={s.t} className="grid grid-cols-[3rem_1fr] gap-4 border-b py-7 last:border-0 md:grid-cols-[5rem_1fr]">
              <span className="font-display text-4xl font-extrabold text-accent [-webkit-text-stroke:1px_hsl(38_70%_38%)] md:text-5xl">{String(i + 1).padStart(2, '0')}</span>
              <div>
                <h2 className="font-display text-xl font-bold">{s.t}</h2>
                {s.body.map((b) => <p key={b} className="mt-2 leading-relaxed text-muted-foreground">{b}</p>)}
                {s.code && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {s.code.map((c) => <code key={c} className="rounded-md bg-ink px-2.5 py-1 font-mono text-sm text-accent">{c}</code>)}
                  </div>
                )}
                {i === 0 && <InstallCta location="start_here_guide" className="mt-4" tone="light" />}
              </div>
            </li>
          ))}
        </ol>
        <div className="mt-6 rounded-xl border bg-secondary/60 p-6">
          <h2 className="font-display text-lg font-bold">Protections to know about</h2>
          <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm text-muted-foreground marker:text-accent">
            <li>Every private request re-checks your current server, roles and membership in Discord. If Discord cannot be reached, access is refused.</li>
            <li>Attendance respects commitments, kickoff time and approved leave.</li>
            <li>Drafts belong to the staff member who started them and need an explicit save.</li>
            <li>If a change is interrupted, check the existing records before repeating it.</li>
          </ul>
        </div>
        <div className="mt-8 flex gap-3">
          <Button asChild><Link href="/admin" data-testid="link-guide-workspace">Open workspace</Link></Button>
          <Button asChild variant="outline"><Link href="/">Back to features</Link></Button>
        </div>
      </div>
    </PublicLayout>
  );
}
