import { type ReactNode } from 'react';
import { Link } from 'wouter';
import { motion } from 'framer-motion';
import { ArrowRight, ShieldCheck, Radar, FileText, CalendarClock, ClipboardCheck, Users, Target, Terminal } from 'lucide-react';
import { PublicLayout, InstallCta, emblem } from '@/components/public-layout';
import { Button } from '@/components/ui/button';

function Reveal({ children, className, delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  return (
    <motion.div className={className} initial={{ opacity: 0, y: 24 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: '-60px' }} transition={{ duration: 0.6, delay, ease: [0.2, 0.7, 0.2, 1] }}>
      {children}
    </motion.div>
  );
}

function Eyebrow({ n, children }: { n: string; children: ReactNode }) {
  return (
    <p className="mb-3 flex items-center gap-3 font-mono text-xs uppercase tracking-[0.25em] text-[hsl(38_70%_34%)]">
      <span className="text-accent-foreground/0 [-webkit-text-stroke:1px_hsl(38_70%_38%)]">{n}</span>{children}
    </p>
  );
}

const features = [
  { icon: Radar, title: 'Scouting and scoring', body: 'Every dossier is built from recorded sessions and the five-category rubric your staff filled in. Compare only players scored on compatible rubrics. Nothing is projected.' },
  { icon: FileText, title: 'Trusted text recaps, not OCR', body: 'Match recaps arrive as text a staff member vouches for. Club Agent never reads screenshots and guesses at the numbers.' },
  { icon: CalendarClock, title: 'Scheduling and availability', body: 'Create, edit, cancel and roster events. Players state availability and file leave; staff review it with the decision on the record.' },
  { icon: ClipboardCheck, title: 'Attendance with protections', body: 'Marks respect commitments, kickoff time and approved leave, so a holiday never reads as a no-show.' },
  { icon: Target, title: 'Staff-only goals and tasks', body: 'Development goals and task lists live behind staff access. Players and visitors see none of it.' },
];

export default function Landing() {
  return (
    <PublicLayout>
      <section className="relative overflow-hidden bg-ink text-[hsl(40_30%_92%)]">
        <div className="pointer-events-none absolute -right-40 -top-40 h-[620px] w-[620px] rounded-full bg-[hsl(214_100%_50%/0.16)] blur-3xl" />
        <div className="mx-auto grid max-w-6xl items-center gap-10 px-4 pb-20 pt-14 md:grid-cols-[1.15fr_0.85fr] md:pt-24">
          <div>
            <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.1 }} className="mb-5 font-mono text-xs uppercase tracking-[0.3em] text-accent">
              FC Pro Clubs / staff companion
            </motion.p>
            <motion.h1 initial={{ opacity: 0, y: 30 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.8, ease: [0.2, 0.7, 0.2, 1] }} className="font-display text-5xl font-extrabold leading-[1.02] tracking-tight md:text-7xl">
              The record <span className="text-accent">speaks</span>.<br />Staff decide.
            </motion.h1>
            <motion.p initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 }} className="mt-6 max-w-xl text-lg leading-relaxed opacity-85">
              Explainable evidence, accountable club operations and attendance that understands vacations. No invented potential ratings, no black-box verdicts on your players.
            </motion.p>
            <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.5 }} className="mt-8 flex flex-wrap items-start gap-4">
              <InstallCta />
              <Button asChild variant="outline" size="lg" className="border-[hsl(40_30%_90%/0.35)] bg-transparent text-inherit">
                <Link href="/guide" data-testid="link-hero-guide">Start here <ArrowRight /></Link>
              </Button>
            </motion.div>
          </div>
          <motion.div initial={{ opacity: 0, scale: 0.92, rotate: -3 }} animate={{ opacity: 1, scale: 1, rotate: 0 }} transition={{ duration: 0.9, ease: [0.2, 0.7, 0.2, 1] }} className="relative mx-auto w-full max-w-sm">
            <div className="absolute -inset-3 rounded-[2.2rem] border border-accent/40" />
            <div className="absolute -inset-7 rounded-[2.8rem] border border-accent/15" />
            <img src={emblem} alt="Club Agent emblem: a gilded Anubis helm above the words Clubs Agent" className="relative aspect-square w-full rounded-[2rem] object-cover shadow-2xl" />
          </motion.div>
        </div>
        <div className="hieroglyph-rule" />
      </section>

      <section className="mx-auto max-w-6xl px-4 py-20">
        <Reveal>
          <Eyebrow n="01">Why it is different</Eyebrow>
          <h2 className="max-w-3xl font-display text-3xl font-bold leading-tight md:text-5xl">Other tools guess a ceiling. Club Agent shows its working.</h2>
        </Reveal>
        <div className="mt-10 grid gap-px overflow-hidden rounded-xl border bg-border md:grid-cols-2">
          <Reveal className="bg-card p-7">
            <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground">What it will not do</p>
            <ul className="mt-4 space-y-3 text-[15px]">
              <li>Invent an AI potential grade for a player.</li>
              <li>Read a screenshot and present a guess as data.</li>
              <li>Promote anyone automatically.</li>
              <li>Publish player records to the open web.</li>
            </ul>
          </Reveal>
          <Reveal className="bg-primary p-7 text-primary-foreground" delay={0.1}>
            <p className="font-mono text-xs uppercase tracking-widest text-accent">What it does instead</p>
            <ul className="mt-4 space-y-3 text-[15px]">
              <li>Shows the sessions, scores and evidence behind every dossier line.</li>
              <li>Takes text recaps a named staff member vouches for.</li>
              <li>Surfaces who qualifies; a human makes the call.</li>
              <li>Keeps everything inside a staff-only, Discord-verified workspace.</li>
            </ul>
          </Reveal>
        </div>
      </section>

      <section className="border-y bg-secondary/50">
        <div className="mx-auto max-w-6xl px-4 py-20">
          <Reveal><Eyebrow n="02">What is inside</Eyebrow><h2 className="font-display text-3xl font-bold md:text-5xl">Built for the people who run the club.</h2></Reveal>
          <div className="mt-10 grid gap-5 md:grid-cols-6">
            {features.map((f, i) => (
              <Reveal key={f.title} delay={i * 0.06} className={i < 2 ? 'md:col-span-3' : 'md:col-span-2'}>
                <div className="group h-full rounded-xl border bg-card p-6 transition-transform duration-300 hover:-translate-y-1">
                  <f.icon className="mb-4 h-6 w-6 text-[hsl(38_75%_38%)] transition-transform group-hover:scale-110" />
                  <h3 className="font-display text-lg font-bold">{f.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{f.body}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-20">
        <Reveal><Eyebrow n="03">Recruitment path</Eyebrow><h2 className="max-w-2xl font-display text-3xl font-bold md:text-5xl">Three tiers. One human decision between each.</h2></Reveal>
        <div className="mt-12 grid gap-0 md:grid-cols-3">
          {[['Trialist', 'Observed, scored, and given sessions to prove availability.'], ['Academia Prospect', 'Cleared attendance and evidence review; developing under named goals.'], ['First Team Roster', 'Promoted by a staff member after reviewing the record.']].map(([t, d], i) => (
            <Reveal key={t} delay={i * 0.1} className="relative border-l-2 border-accent py-2 pl-6 md:border-l-0 md:border-t-2 md:pl-0 md:pt-6">
              <span className="absolute -left-[7px] top-3 h-3 w-3 rounded-full bg-accent md:-top-[7px] md:left-0" />
              <p className="font-mono text-xs text-muted-foreground">Tier {i + 1}</p>
              <h3 className="font-display text-xl font-bold">{t}</h3>
              <p className="mt-1 max-w-xs text-sm text-muted-foreground">{d}</p>
            </Reveal>
          ))}
        </div>
      </section>

      <section className="bg-primary text-primary-foreground">
        <div className="mx-auto grid max-w-6xl items-center gap-10 px-4 py-20 md:grid-cols-[1fr_1fr]">
          <Reveal>
            <Eyebrow n="04"><span className="text-accent">The current standard</span></Eyebrow>
            <h2 className="font-display text-3xl font-bold md:text-5xl">Show up, on the record.</h2>
            <p className="mt-4 max-w-md opacity-85">The default promotion standard is adjustable per club. Meeting it makes a player eligible for review. It never promotes them.</p>
          </Reveal>
          <Reveal delay={0.1} className="grid grid-cols-3 gap-3 text-center">
            {[['80%', 'attendance'], ['30', 'days'], ['5', 'marked sessions']].map(([a, b]) => (
              <div key={b} className="rounded-xl border border-accent/40 bg-ink/50 px-2 py-6">
                <p className="font-display text-4xl font-extrabold text-accent md:text-5xl">{a}</p>
                <p className="mt-1 text-xs uppercase tracking-widest opacity-80">{b}</p>
              </div>
            ))}
            <p className="col-span-3 text-left text-xs opacity-70">Approved leave and commitments are protected; see the attendance rules in the guide.</p>
          </Reveal>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-4 py-20">
        <div className="grid gap-10 md:grid-cols-2">
          <Reveal>
            <Eyebrow n="05">Discord stays the front door</Eyebrow>
            <h2 className="font-display text-3xl font-bold md:text-4xl">Publishing happens where your players are.</h2>
            <p className="mt-4 text-muted-foreground">The website is the private workspace. Public-facing posts are not published from here. Launch the panel and the docket from Discord itself.</p>
          </Reveal>
          <Reveal delay={0.1} className="space-y-3">
            {[['/start_panel', 'Launches the member panel. Manager-only, in Discord.'], ['/scout docket', 'Posts the docket. Never republished automatically.']].map(([c, d]) => (
              <div key={c} className="flex items-start gap-3 rounded-xl border bg-card p-4">
                <Terminal className="mt-0.5 h-5 w-5 text-[hsl(38_75%_38%)]" />
                <div><p className="font-mono text-sm font-medium">{c}</p><p className="text-sm text-muted-foreground">{d}</p></div>
              </div>
            ))}
            <div className="flex items-start gap-3 rounded-xl border bg-card p-4">
              <ShieldCheck className="mt-0.5 h-5 w-5 text-[hsl(38_75%_38%)]" />
              <p className="text-sm text-muted-foreground">Signing up never grants access. Your Discord account is linked with a one-time code, and every request re-checks your current staff roles.</p>
            </div>
          </Reveal>
        </div>
      </section>

      <section className="bg-ink text-[hsl(40_30%_92%)]">
        <Reveal className="mx-auto flex max-w-6xl flex-col items-start gap-6 px-4 py-20 md:flex-row md:items-center md:justify-between">
          <div>
            <Users className="mb-3 h-6 w-6 text-accent" />
            <h2 className="font-display text-3xl font-bold md:text-4xl">Bring the bot. Link your account. Start with the guide.</h2>
          </div>
          <div className="flex flex-col gap-3"><InstallCta /><Link href="/guide" className="text-sm underline underline-offset-4 hover:text-accent">Read the start-here guide</Link></div>
        </Reveal>
      </section>
    </PublicLayout>
  );
}
