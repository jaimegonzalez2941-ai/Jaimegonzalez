# Companion website capability contract

SQLite remains the source of truth. The website never opens it. Express verifies
Clerk cookies and passes the verified subject through a mode-0600 Unix socket to
the running bot. The bot resolves a subject's Discord identity only from a
single-use challenge redeemed by that Discord account. Registration grants no
server permissions. Every private operation fetches current guild, roles and
individual member from Discord; unavailable authorization fails closed.

| Screen / workflow | Existing application boundary | Access / safety |
|---|---|---|
| Overview | saved settings, upcoming events, staff records, pending leave, audit | current staff; live connection separate from saved data |
| Setup / diagnostics | `scout setup`, `scout status`, `scout stage_role`, `promotion policy`, `schedule notifications` | manager settings; existing staff notification permission preserved; private-channel checks unchanged |
| Schedule | `schedule create/edit/cancel/assign/roster/show/list` | staff, explicit cancellation, repository reconfirmation and notification revision rules |
| Availability / leave | `availability roster/view/leaves/review_leave` | staff-only website; existing review audit |
| Attendance / promotion | `attendance mark/report`, `promotion review` | existing commitments, kickoff and leave protections; human promotion |
| Scouting | `scout player/history/stats/compare/link/unlink/ingest` | staff; bounded lists, compatible rubric comparison; unlink confirmation |
| Evaluations | `start_draft`, `draft`, `change_draft` | actor-owned drafts, revision checks, full rubric validation and explicit save/discard |
| Recruitment | `scout shortlist add/list/remove`, `scout assign` | eligible staff owners; archive confirmation; Manage Roles, hierarchy, evidence and override checks |
| Staff goals | `development goal/edit/review/complete` | existing validation, atomic audit and idempotent completion |
| Tasks / briefing | `assistant task add/edit/list/complete`, `assistant brief` | staff; on-demand only; no invented evidence |
| Launch panel | `/start_panel` in Discord | manager-only linked workflow; preserve uncertain-publication reconciliation |
| Docket | `/scout docket` in Discord | clearly linked; no automatic republishing |

All implemented operations have catalogued form fields derived from the Discord
command definitions; command callbacks run existing checks and repository writes.
Interactive Discord guide/panel controls stay clearly linked to Discord.
Web evidence pages use bounded record queries. Long dossier/briefing pages reuse
the existing lazy snapshot renderer and reauthorize each continuation.
No sample club data or automatic membership/role grants are introduced.
