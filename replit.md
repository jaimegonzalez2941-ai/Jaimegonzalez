# Club Agent

An always-on Discord bot and companion website for private FC Pro Clubs scouting, recruitment, availability, schedules, attendance, and match-performance tracking.

## Run & Operate

- `uv run python -m scoutbot` — start the bot using the configured `Scout Discord Bot` workflow
- `uv run python -m scoutbot --check` — validate database setup and slash commands without signing in
- `uv run python -m scoutbot --backup` — create a private SQLite backup, including WAL data
- `uv run python -m scoutbot --rehearse-restore BACKUP` — verify a backup on an isolated copy
- `uv run python -m scoutbot --restore BACKUP --confirm-live-restore` — explicitly restore after stopping the bot/API
- `uv run python -m unittest discover -s tests -v` — run bot unit and persistence tests
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 8080)
- `pnpm --filter @workspace/club-agent run dev` — companion site; use its managed workflow for preview environment/routing
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- Do not push the template PostgreSQL schema for club data. SQLite remains authoritative.
- Required bot secret: `DISCORD_TOKEN` — Replit Secret; never print or commit its value
- Automatic recaps require **Message Content Intent** enabled in the Discord Developer Portal
- Bot database defaults to `.data/scout-v2.db` (ignored by Git); set `SCOUT_DB_PATH` to change it
- Operator backups default to `.data/backups/` (private mode 0700 directory / 0600 files); backups are not exposed through the API and are not automatically scheduled or copied off-host
- Set `SCOUT_BACKUP_DIR` to choose a different private operator backup directory
- See `README.md` for installation, permissions, setup, and the supported recap format

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- Python 3.12, `discord.py`, `aiosqlite`
- API: Express 5
- Club DB: existing SQLite through Python; the unused template PostgreSQL library is not a migration target
- Website: React/Vite, Clerk cookie sessions, verified Discord account linking
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `scoutbot/` — bot lifecycle, SQLite repository, Discord commands, parser, and permissions
- `scoutbot/web_*.py` — local application boundary, current Discord authorization, website read models and command adapters
- `artifacts/club-agent/` — public site and protected staff workspace; `mockup-sandbox` remains a sandbox
- `docs/website-capabilities.md` — screen-to-operation contract and permission map
- `tests/` — parser, authorization, database, and Discord command contract tests
- `README.md` — bot installation and server configuration

## Architecture decisions

- Server-scoped slash commands check staff access at runtime; server managers bootstrap with `/scout setup`.
- Recaps require the configured source and channel; imports are atomic and idempotent.
- Missing match metrics stay unreported instead of being stored as zero.
- V2 uses its own SQLite database; it never modifies V1 data.
- Position-aware scoring uses immutable `staff-v1` design defaults, not a validated predictor. See `docs/scouting-rubric.md` for weights, observable anchors, rounding and examples.
- Incomplete/borderline scorecards remain persisted private drafts. All five categories, observed evidence, context and positive declared coverage are required to save. Reopen evaluate with the same candidate/position to resume.
- Any rubric change requires a new version, retaining old calculation/rendering behavior. Neither raw nor rounded totals in 6.8–7.2 may be published. Legacy records are never recalculated.
- Declared coverage, trusted imported EA statistics, human scores, and recruitment stages stay distinct; a rating never authorizes or automatically assigns a Discord role.

## Product

- Private scorecards, scouting history, staff docket, recruitment roles, EA handle linking, match statistics, weekly availability, approved leave, schedules, RSVPs, attendance, and promotion evidence.
- `/club` is the private Start Here workflow menu, with a categorized reference. `/scout` is retained for backward-compatible staff commands.
- Coach/owner operations are available in Discord and the private companion site. Both use the bot's repositories, validation and audit history. The own-account player view shows only explicitly shared goal text, status and deadline; private scouting and staff evidence never become player authority.
- Staff workspace mutations and audits share a serialized transaction; IDs are server-local. Recruitment deadlines are follow-ups, not player decisions; completion never changes scores or roles.
- Workspace views contain saved evidence only, paginate full observations, and recheck staff access, requesting user and server on each continuation. Assignees use a current single-member REST check, never privileged member enumeration.
- Briefings are on-demand only, preserve current attendance protections, and do not depend on session notification delivery.
- Website signup grants no admin role. Clerk identity is linked by a five-minute, single-use `/website code:...` challenge redeemed by the Discord account itself. Never use a supplied Discord ID or workspace connector as actor authority.
- Every website read/write rechecks current Discord roles, owner and individual membership through REST. Failed/unavailable verification denies access. Private API responses are `no-store`; browser writes require same-origin JSON.
- Express communicates with the running bot through `.data/club-web/bridge.sock` in a mode-0700 directory with a mode-0600 socket. There is no TCP bridge, raw SQL API or second SQLite connection in Node. Run both services as the same OS account on the same host.
- SQLite recovery requires stopping the bot/API, rehearsing the selected backup first, and explicitly confirming live restore. Restore preserves a pre-restore backup; see the README for operator steps, retention assumptions and limits.
- `CLUB_WEB_SOCKET` is an optional local socket-path override. Never point the normal API workflow at a test fixture. Browser fixtures in `tests/web_browser_fixture.py` run explicitly with temporary databases; no test bypass exists in application code.
- See README for attendance policy, vacation protections, timezone behavior, role-override rules, and command examples.

## User preferences

_No additional preferences recorded._

## Gotchas

- Enable Message Content Intent before running V2 with automatic recaps enabled.
- Confirm the recap source matches `README.md`; unsupported formats are rejected, not guessed.
- The bot needs a continuously running single host and a durable filesystem. Replit published filesystems are ephemeral, including VM deployments; the current separate web/API artifacts cannot share the bot's local Unix socket. This repo has no verified supported production topology until hosting is consolidated on a durable host or a separate database migration is completed.
- Website live operations require the bot workflow too; a static frontend alone cannot administer the club. Do not claim saved settings prove current privacy, source authenticity, intent configuration or successful delivery.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
