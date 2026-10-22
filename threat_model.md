# Threat Model

## Project Overview

Club Agent is an always-on Discord bot (Python 3.12, `discord.py`, `aiosqlite`) with a
companion website for private FC Pro Clubs scouting, recruitment, availability, schedules,
attendance, and match-performance tracking. The website is a React/Vite SPA
(`artifacts/club-agent`) authenticated with Clerk cookie sessions, served alongside an
Express 5 API server (`artifacts/api-server`, port 8080). The API server owns no club data
or bot credentials; it forwards authenticated requests to the running bot over a local Unix
domain socket (`.data/club-web/bridge.sock`). SQLite (via the Python bot) is the single
authoritative data store. `artifacts/mockup-sandbox` is a design sandbox, dev-only.

Users are Discord club staff (coaches/owners/managers) and individual players. Website
identity is a Clerk account that must be explicitly linked to a Discord account through a
single-use, five-minute `/website code:...` challenge redeemed by the Discord account itself.

## Assets

- **Private scouting evidence and evaluations** — scorecards, rubric scores, observations,
  recruitment stages. Highly sensitive staff-only data.
- **Player PII** — Discord IDs, display names, availability/leave windows, attendance.
- **Discord account linkage** — the `web_discord_links` mapping of Clerk subject → Discord ID.
- **Application secrets** — `DISCORD_TOKEN`, `CLERK_SECRET_KEY`, Clerk publishable key
  (publishable key is intentionally public).
- **Operator backups** — private SQLite backups under `.data/backups/` (mode 0700/0600).

## Trust Boundaries

- **Browser → Express API** — all website requests. Express authenticates via Clerk
  (`getAuth(req).userId`) and never trusts client-supplied identity or server/guild scope
  as authority. Mutations additionally require same-origin + JSON (CSRF defense).
- **Express → Bot (Unix socket)** — Express holds no credentials and runs no SQL. The socket
  is restricted by filesystem mode (0700 dir / 0600 socket) to the same OS account. The bot
  re-derives the Discord actor from the Clerk subject and re-checks all authorization.
- **Bot → Discord REST** — every website read/write re-verifies current membership, roles,
  owner status, and channel privacy via live REST calls; gateway/member caches are never
  used as authorization authority.
- **Bot → SQLite** — all queries are parameterized; records are scoped by `guild_id` plus
  the acting member/scout where ownership applies.
- **Public vs authenticated vs staff vs manager** — `/api/club/public` is unauthenticated
  (bot connectivity + install URL only). All other endpoints require a linked, authenticated
  subject; staff screens require `staff_access`; configuration requires `manage_guild`.

## Scan Anchors

- Production entry points:
  - `artifacts/api-server/src/routes/club.ts` — the only data-bearing HTTP surface.
  - `artifacts/api-server/src/app.ts` — Clerk middleware, JSON 64kb limit, Clerk FAPI proxy.
  - `scoutbot/web_bridge.py` — Unix-socket dispatch, subject verification, rate limiting.
  - `scoutbot/web_context.py` / `scoutbot/access.py` — live REST authorization rechecks.
  - `scoutbot/web_workspace.py` / `scoutbot/web_drafts.py` — scoped read/write models.
- Highest-risk areas: identity linking (`scoutbot/web_identity.py`), authorization helpers
  (`scoutbot/access.py`), SQL in `scoutbot/database.py` and `web_workspace.py`.
- Dev-only / ignore unless proven reachable: `artifacts/mockup-sandbox/**`,
  `tests/**`, operator CLI (`scoutbot/backup_ops.py`, `scoutbot/__main__.py`).

## Threat Categories

### Spoofing
Website authority is only granted after a Clerk subject is linked to a Discord ID via a
single-use, time-limited, cooldown-protected code redeemed by the Discord account itself
(`web_identity.py`). Relinking to a different Discord identity is refused. The bot rejects
any subject not shaped like a Clerk `user_` id and never trusts a client-supplied Discord
ID or guild as actor authority. Required guarantee: website authority must always derive
from the authenticated Clerk subject mapped through `web_discord_links`, then re-verified
against live Discord membership.

### Tampering
Mutations require same-origin Origin matching the host and a JSON content-type
(`browserOrigin.ts`), defeating cross-site POSTs. Request bodies are validated with strict
Zod schemas (`.strict()`) and re-validated in Python. SQL is fully parameterized; `LIKE`
searches escape wildcards. Guarantee: all writes must pass server-side schema validation,
same-origin checks, and parameterized persistence.

### Information Disclosure
Private responses are `Cache-Control: no-store, private`, `Vary: Cookie, Origin`,
`X-Content-Type-Options: nosniff`, and no CORS headers are emitted (cross-site reads cannot
observe bodies). Workspace/evaluation queries are scoped by `guild_id` and, where ownership
applies, by the acting scout/member (e.g. `evaluation_drafts ... scout_id=?`, player
development `member_id=?`). The player view projects only explicitly shared goal text/status.
Error responses are generic with opaque operator references. Guarantee: each sensitive
record access must be scoped to the caller's verified membership and ownership boundary.

### Elevation of Privilege
Staff screens enforce `staff_access` (live role/manager check); configuration enforces
`manage_guild`; confirm-gated and manager-gated operations are checked in `web_bridge.py`
before invocation. Recruitment role assignment refuses privileged/administrative roles and
roles above the actor's or bot's top role (`access.py`). The Clerk FAPI proxy targets a
fixed host (no SSRF). Guarantee: every privileged action must be re-authorized server-side
against live Discord state at request time.

### Denial of Service
Per-subject rate limiting (90 req/min), 64kb JSON body limit, 128kb socket request cap,
512kb response cap, bounded snapshot/challenge tables, per-actor write locks, and bounded
guild enumeration (≤200) limit resource abuse. Guarantee: unauthenticated and authenticated
surfaces must retain request-size and rate bounds.

## Notes

As of the current baseline (`7efd250`), no actionable production vulnerabilities were
identified. Deployment info reports the project is not currently deployed, and `replit.md`
notes there is no verified supported production topology (the web/API artifacts cannot share
the bot's local Unix socket on Replit's ephemeral filesystem) — a hosting/operational
constraint rather than a code-level security vulnerability.
