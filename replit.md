# FC Scouting & Performance Bot V2

An always-on Discord bot for private FC Pro Clubs scouting, recruitment, and match-performance tracking.

## Run & Operate

- `uv run python -m scoutbot` — start the bot using the configured `Scout Discord Bot` workflow
- `uv run python -m scoutbot --check` — validate database setup and slash commands without signing in
- `uv run python -m unittest discover -s tests -v` — run bot unit and persistence tests
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required bot secret: `DISCORD_TOKEN` — Replit Secret; never print or commit its value
- Automatic recaps require **Message Content Intent** enabled in the Discord Developer Portal
- Bot database defaults to `.data/scout-v2.db` (ignored by Git); set `SCOUT_DB_PATH` to change it
- See `README.md` for installation, permissions, setup, and the supported recap format

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- Python 3.12, `discord.py`, `aiosqlite`
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `scoutbot/` — bot lifecycle, SQLite repository, Discord commands, parser, and permissions
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

- Private scorecards, scouting history, staff docket, recruitment roles, EA handle linking, and match statistics.

## User preferences

_No additional preferences recorded._

## Gotchas

- Enable Message Content Intent before running V2 with automatic recaps enabled.
- Confirm the recap source matches `README.md`; unsupported formats are rejected, not guessed.
- A published bot needs a continuously running VM and durable file storage.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
