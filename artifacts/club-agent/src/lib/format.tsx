import { Fragment, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function formatTimestamp(sec: number, style = 'f'): string {
  const d = new Date(sec * 1000);
  if (Number.isNaN(d.getTime())) return String(sec);
  const f = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat(undefined, o).format(d);
  switch (style) {
    case 't': return f({ timeStyle: 'short' });
    case 'T': return f({ timeStyle: 'medium' });
    case 'd': return f({ dateStyle: 'short' });
    case 'D': return f({ dateStyle: 'long' });
    case 'F': return f({ dateStyle: 'full', timeStyle: 'short' });
    case 'R': {
      const diff = sec - Date.now() / 1000;
      const abs = Math.abs(diff);
      const units: [Intl.RelativeTimeFormatUnit, number][] = [
        ['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60],
      ];
      const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
      for (const [u, s] of units) if (abs >= s) return rtf.format(Math.round(diff / s), u);
      return rtf.format(Math.round(diff), 'second');
    }
    default: return f({ dateStyle: 'long', timeStyle: 'short' });
  }
}

const INLINE =
  /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))|(<t:(-?\d+)(?::([tTdDfFR]))?>)|(<(@&|@!|@|#)(\d{5,25})>)/g;

const MENTION_LABEL: Record<string, string> = { '@': 'Member', '@!': 'Member', '@&': 'Role', '#': 'Channel' };

export function Inline({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const m of text.matchAll(INLINE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(text.slice(last, idx));
    const k = n++;
    if (m[1]) out.push(<code key={k} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{m[1].slice(1, -1)}</code>);
    else if (m[2]) out.push(<strong key={k} className="font-semibold">{m[2].slice(2, -2)}</strong>);
    else if (m[3]) out.push(<em key={k}>{m[3].slice(1, -1)}</em>);
    else if (m[4]) {
      const mm = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(m[4]);
      if (mm) out.push(<a key={k} href={mm[2]} target="_blank" rel="noopener noreferrer nofollow" className="text-[hsl(214_90%_40%)] underline underline-offset-2">{mm[1]}</a>);
    } else if (m[5]) {
      const sec = Number(m[6]);
      const timestamp = new Date(sec * 1000);
      if (Number.isNaN(timestamp.getTime())) {
        out.push(<span key={k}>{m[5]}</span>);
        last = idx + m[0].length;
        continue;
      }
      out.push(
        <time key={k} dateTime={timestamp.toISOString()} title={formatTimestamp(sec, 'F')} className="rounded bg-accent/25 px-1 font-medium">
          {formatTimestamp(sec, m[7] || 'f')}
        </time>,
      );
    } else if (m[8]) {
      out.push(
        <span key={k} className="whitespace-nowrap rounded bg-primary/10 px-1 font-mono text-[0.85em]" title="Discord ID">
          {MENTION_LABEL[m[9]]} {m[10]}
        </span>,
      );
    }
    last = idx + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}

const isBlockStart = (l: string) =>
  /^```/.test(l) || /^#{1,4}\s/.test(l) || /^\s*([-*•])\s+/.test(l) || /^\s*\d+[.)]\s+/.test(l) || /^>\s?/.test(l) || /^\s*\|.*\|\s*$/.test(l) || /^(-{3,}|\*{3,})\s*$/.test(l);

const cells = (l: string) => l.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());

export function Markdown({ text, className }: { text: string; className?: string }) {
  const lines = String(text ?? '').replace(/\r/g, '').split('\n');
  const out: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push(<pre key={k++} className="overflow-x-auto rounded-md bg-ink p-3 font-mono text-xs leading-relaxed text-[hsl(40_30%_88%)]">{buf.join('\n')}</pre>);
    } else if (!line.trim()) {
      i++;
    } else if (/^#{1,4}\s/.test(line)) {
      const lvl = line.match(/^#+/)![0].length;
      out.push(
        <p key={k++} className={cn('font-display font-semibold tracking-wide text-foreground', lvl <= 2 ? 'mt-2 text-base' : 'mt-1 text-sm')}>
          <Inline text={line.replace(/^#+\s+/, '')} />
        </p>,
      );
      i++;
    } else if (/^(-{3,}|\*{3,})\s*$/.test(line)) {
      out.push(<hr key={k++} className="my-2 border-border" />);
      i++;
    } else if (/^\s*([-*•])\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•])\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*•])\s+/, ''));
      out.push(<ul key={k++} className="ml-4 list-disc space-y-0.5 marker:text-accent">{items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ul>);
    } else if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ''));
      out.push(<ol key={k++} className="ml-5 list-decimal space-y-0.5">{items.map((t, j) => <li key={j}><Inline text={t} /></li>)}</ol>);
    } else if (/^>\s?/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) items.push(lines[i++].replace(/^>\s?/, ''));
      out.push(<blockquote key={k++} className="border-l-2 border-accent pl-3 text-muted-foreground"><Inline text={items.join(' ')} /></blockquote>);
    } else if (/^\s*\|.*\|\s*$/.test(line)) {
      const rows: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        if (!/^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i]) || !/-/.test(lines[i])) rows.push(cells(lines[i]));
        i++;
      }
      const [head, ...body] = rows;
      if (!head) continue;
      out.push(
        <div key={k++} className="overflow-x-auto rounded-md border">
          <table className="w-full text-left text-xs">
            <thead className="bg-muted"><tr>{head.map((c, j) => <th key={j} className="px-2 py-1.5 font-semibold"><Inline text={c} /></th>)}</tr></thead>
            <tbody>{body.map((r, j) => <tr key={j} className="border-t">{r.map((c, x) => <td key={x} className="px-2 py-1.5 align-top"><Inline text={c} /></td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
    } else {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim() && !(buf.length && isBlockStart(lines[i]))) buf.push(lines[i++]);
      out.push(
        <p key={k++}>
          {buf.map((t, j) => (
            <Fragment key={j}>{j > 0 && <br />}<Inline text={t} /></Fragment>
          ))}
        </p>,
      );
    }
  }
  return <div className={cn('space-y-2 text-sm leading-relaxed [overflow-wrap:anywhere]', className)}>{out}</div>;
}

export function humanize(key: string): string {
  return key
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_.-]+/g, ' ')
    .trim()
    .replace(/^./, (c) => c.toUpperCase());
}

const ISO = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$/;
const DATE_KEY = /(_at|At|_time|time|date|Date|until|since|start|end|kickoff|due)$/;

export function Value({ v, k, depth = 0 }: { v: unknown; k?: string; depth?: number }): ReactNode {
  if (v === null || v === undefined || v === '') return <span className="text-muted-foreground">none</span>;
  if (typeof v === 'boolean') return <span className="font-medium">{v ? 'Yes' : 'No'}</span>;
  if (typeof v === 'number') {
    if (k && DATE_KEY.test(k) && v > 1e9) {
      const sec = v > 1e12 ? v / 1000 : v;
      return <time title={formatTimestamp(sec, 'F')}>{formatTimestamp(sec, 'f')}</time>;
    }
    return <span className="tabular-nums">{v.toLocaleString()}</span>;
  }
  if (typeof v === 'string') {
    if (ISO.test(v)) {
      const d = new Date(v.includes('T') || v.length <= 10 ? v : v.replace(' ', 'T'));
      if (!Number.isNaN(d.getTime())) {
        const dateOnly = v.length <= 10;
        return <time dateTime={d.toISOString()}>{new Intl.DateTimeFormat(undefined, dateOnly ? { dateStyle: 'medium', timeZone: 'UTC' } : { dateStyle: 'medium', timeStyle: 'short' }).format(d)}</time>;
      }
    }
    if (v.includes('\n') || v.length > 160) return <Markdown text={v} />;
    return <Inline text={v} />;
  }
  if (Array.isArray(v)) {
    if (!v.length) return <span className="text-muted-foreground">none</span>;
    const prim = v.every((x) => x === null || typeof x !== 'object');
    if (prim) {
      return (
        <span className="flex flex-wrap gap-1">
          {v.slice(0, 12).map((x, i) => (
            <span key={i} className="rounded bg-muted px-1.5 py-0.5 text-xs"><Value v={x} k={k} depth={depth + 1} /></span>
          ))}
          {v.length > 12 && <span className="text-xs text-muted-foreground">+{v.length - 12} more</span>}
        </span>
      );
    }
    return (
      <ul className="space-y-1">
        {v.slice(0, 5).map((x, i) => <li key={i} className="rounded border bg-background/60 p-1.5"><Value v={x} depth={depth + 1} /></li>)}
        {v.length > 5 && <li className="text-xs text-muted-foreground">+{v.length - 5} more</li>}
      </ul>
    );
  }
  if (typeof v === 'object') {
    const entries = Object.entries(v as Record<string, unknown>);
    if (depth >= 2) return <span className="text-muted-foreground">{entries.length} fields</span>;
    return (
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        {entries.slice(0, 8).map(([ek, ev]) => (
          <Fragment key={ek}>
            <dt className="text-muted-foreground">{humanize(ek)}</dt>
            <dd><Value v={ev} k={ek} depth={depth + 1} /></dd>
          </Fragment>
        ))}
      </dl>
    );
  }
  return <span>{String(v)}</span>;
}

export function errorText(e: unknown): string {
  const x = e as { status?: number; data?: { error?: string; message?: string } | null; message?: string } | null;
  const m = x?.data?.error || x?.data?.message || x?.message;
  return m ? String(m) : 'The service did not respond.';
}
export function errorStatus(e: unknown): number | undefined {
  return (e as { status?: number } | null)?.status;
}
