# Club Agent

A continuously running Discord bot for private player scouting, recruitment, availability, scheduling, attendance, and trusted match recaps. The original bot script and any earlier database have not been changed. Existing V2 records remain at `.data/scout-v2.db`; this update adds tables without replacing the database.

The bot applies the **Club Agent** Discord username and the supplied avatar on startup. The image is bundled locally; a stored image fingerprint prevents unnecessary avatar updates on each restart. A failed Discord identity update is logged without disabling club commands. Existing custom server nicknames are updated where Discord permits.

## Start the bot

- `DISCORD_TOKEN` must be saved as a Replit Secret. Do not put it in source, configuration, or Discord messages.
- For full automatic recap reading, open the **Bot** page of this bot's application in the [Discord Developer Portal](https://discord.com/developers/applications) and enable **Message Content Intent**. Grant this bot Message Content Intent as a privileged gateway intent, then restart it. Without this, Discord rejects the bot's gateway connection.
- Click **Run** for the **Scout Discord Bot** workflow. When it connects, the workflow log prints a bot-invite link. Open that link to add V2 to your server with the requested channel, message, embed, history, and role permissions.
- `/club`, `/scout`, `/availability`, `/schedule`, `/attendance`, and `/promotion` sync globally and may take Discord a little time to appear. Set `DISCORD_TEST_GUILD_ID` before startup to sync to one server immediately while testing.
- In Discord, a server manager runs `/scout setup`: choose a dedicated staff role, a staff-only channel, the channel that receives recaps, and either a trusted bot in your server or the exact numeric ID of a webhook. Setup rejects channels that explicitly allow non-staff viewers.
- Add recruitment roles to candidates only after a manager maps each stage using `/scout stage_role`. The bot adds the chosen role and deliberately does not remove any existing roles.
- Scouts map player accounts with `/scout link`. Then use `/scout evaluate`, `/scout history`, `/scout stats`, or `/scout docket`.

## Availability, vacation, and scheduling

Run `/club` for the full command guide. Club-operation responses are private to the caller. Players can view their own availability, leave, and attendance; reviewing other players requires the configured staff role or Manage Server. Session details are visible to server members through slash commands. No new privileged member-list intent is required.

### Player commands

- `/availability set weekday start end timezone` sets one weekly window per weekday, replacing that day's previous window. Use 24-hour `HH:MM` and an IANA timezone such as `America/Chicago` or `Europe/London`. `24:00` is allowed as an end time; split an overnight window across two days. Weekly windows are planning context, not a commitment or scoring penalty.
- `/availability view` shows your windows; `/availability clear weekday` removes one.
- `/availability leave start_date end_date timezone` requests vacation or time away, using `YYYY-MM-DD` and an **inclusive last day**. No private reason is requested. Leave is pending until staff approve it.
- `/availability leaves` shows your request IDs and statuses, ten per page. `/availability cancel_leave leave_id` cancels your request. Only staff can change approved leave that has already started.
- `/schedule list` shows upcoming/ongoing sessions; use `past:True` for history and `page` for pagination. `/schedule show session_id` shows your RSVP and overlapping leave.
- `/schedule rsvp session_id response` lets you commit **yes**, respond **maybe**, or decline **no**, until kickoff. A staff assignment cannot be removed by toggling your RSVP; ask staff to release it.
- `/attendance report` shows your attendance under the current policy window. Optional `days` provides a different reporting window without changing promotion policy.

### Staff commands

- `/availability roster` lists recurring windows. `/availability view member`, `/availability leaves member`, or `/availability leaves all_players:True` supports private staff review. All lists have page options.
- `/availability review_leave leave_id approved` approves or declines/revokes a request. Changes are audited. Approved leave excludes overlapping sessions from the player's attendance denominator, including already recorded sessions.
- `/schedule create title kind date time timezone minutes` creates a **training**, **match**, or **trial** session. Duration defaults to 90 minutes (15–720 allowed). Session times display through Discord timestamps in each viewer's local timezone.
- `/schedule edit` updates an upcoming session. Changing start or end time resets **all RSVPs and assignments to pending**, so old commitments cannot count against a player at a new time. A title-only edit preserves commitments.
- `/schedule cancel session_id confirm:True` cancels and excludes the session without deleting its records.
- `/schedule assign session_id member` assigns a player before kickoff; `release:True` releases them to pending. `/schedule roster session_id` shows RSVP, attendance, and approved-leave conflicts.
- `/attendance mark session_id member status` marks **present**, **late**, **absent**, **excused**, or **unmarked** after kickoff. Repeating it corrects the previous mark, rather than duplicating attendance, and records an audit event. Only players who committed yes or were assigned before kickoff can be marked.

Session creation/editing uses an explicit timezone, rejecting nonexistent or ambiguous daylight-saving times; use UTC to disambiguate. Cancelled sessions cannot receive RSVPs or attendance marks. RSVPs and assignments close at kickoff, and completed sessions cannot be rescheduled.

## Attendance and development-tier promotion

Development tiers remain **Trialist → Academia Prospect → First Team Roster**.

The starting standard is **80% attendance over the last 30 days, at least five marked eligible sessions**, plus a latest saved scouting evaluation of **8.0 or higher within that window**. This is an explainable recommendation for staff, not a prediction or an automatic role assignment.

- Only completed, non-cancelled sessions starting within the rolling window and carrying a yes/assigned commitment are considered.
- Present and late count as attended; lateness is shown separately.
- The denominator is **present + late + absent**. Excused absences, approved overlapping leave, and unmarked sessions are excluded. Missing attendance is never automatically marked absent.
- No eligible sessions means **insufficient evidence**, not 0% attendance. A displayed rounded percentage does not change the actual threshold comparison.
- Declared weekly availability is not used to lower a scouting score. Pending leave does not exclude a session until staff approve it.
- `/promotion review member` shows the policy, attendance counts, latest evaluation, current mapped tier, and next tier. With multiple additive tier roles, the highest mapped tier is considered current.
- `/promotion policy percent days minimum_sessions` lets a server manager adjust requirements for the club's availability. Bounds are 1–100%, 1–365 days, and 1–100 sessions. Existing scores and marks are retained.
- `/scout assign` still requires staff plus Manage Roles and all existing role safety checks. Initial Trialist placement is allowed without an attendance history. Assigning Academia Prospect or First Team Roster without the evidence minimums requires a deliberate **10–500 character `override_reason`**; evidence, policy, and reason are audited. Staff remain responsible for advancement and role changes. Prior roles are not automatically removed.

These commands do not send scheduled reminders, publish public rosters, track voice-channel presence, or infer attendance automatically. Attendance is a staff-recorded record, not independently verified activity.

## Trusted recap format

V2 reads text from a trusted message's content, embed description, and embed field values. It does not read attached or embedded images. The configured Friday recap posts show player statistics only in a leaderboard image, so those posts are intentionally rejected and do not create match records.

For V2 to import a recap, include each player's statistics as text, one player per line:

```
EAHandle | Rating: 8.4 | Goals: 2 | Assists: 1 | Pass Acc: 88% | Tackles: 3
```

The row must begin with the player's EA handle, followed by pipe-separated `Label: value` fields. Labels are case-insensitive: `Rating`, `Goals`, `Assists`, `Pass Acc` (or `Passing Acc` / `Passing Accuracy`), and `Tackles`. At least one labeled statistic is required. Counts must be whole numbers; passing accuracy may include `%`. Omitted values remain **unreported**, never fabricated as zero. A malformed row, unknown statistic, or duplicate player rejects the entire recap; `/scout status` shows rejected message IDs and reasons. Correct the source format and use `/scout ingest` with the message ID to retry. Imported messages are immutable and duplicate imports are ignored.

For example, a post containing `Club · Cup / Playoff match · Loss 1–5`, a date, and `Current leaderboards` with the player rows only in its image is not importable. Include the player rows as text in the message or embed to make it eligible.

Match ratings are reported EA input, not human scouting scores; the scouting no-7 rule does not alter match data. Match records use Discord's source message timestamp, not an independently verified match time. Linking a player after a recap has already been imported makes earlier matching records visible.

## Commands and limits

- All scouting and match-data responses are private (ephemeral); the persistent docket is restricted to its configured private channel.
- `/scout evaluate` opens a private, position-aware five-category scorecard. Read each category's guidance, enter observed evidence, add context and positive declared session count, then review and **Confirm & save**. Repeating the command with the same candidate/position resumes your draft after timeout/restart. Drafts can be corrected or discarded.
- Category scores accept 1–10 with one decimal place; missing categories remain unassessed. All five are needed for a weighted result. The original overall no-7 policy excludes **6.8–7.2 inclusive** (raw or rounded); borderline results remain drafts requiring reassessment, never snapped or published. Trial-ready begins at a rounded 8.0, not automatic role assignment.
- Evidence coverage is separate: 1–2 declared sessions = Limited, 3–4 = Developing, 5+ = Broader. These are not verified statistical confidence. **Trusted match context** shows imported records separately, never blended into the rating.
- See [the versioned rubric and worked examples](docs/scouting-rubric.md) for role observables, 1–10 anchors, weights and exact decimal half-up rounding. Initial weights are design defaults, not validated predictions. Older single-score evaluations remain labeled legacy, without fabricated subscores or recalculation.
- `/scout unlink` requires a confirmation option and removes only the handle mapping; it **does not** erase statistics. No player-stat deletion command is provided.
- Recruitment role assignment requires Manage Roles for both the actor and bot; roles are validated as non-privileged and below the bot's hierarchy.
- Each server's settings, scouting records, role mapping, match data, and audit history are isolated in the database.
- `ENABLE_RECAPS=false` runs evaluation and recruiting without the privileged Message Content intent. Set it to `true` only after enabling the intent in Discord, then restart.
- `SCOUT_DB_PATH` can select another SQLite file path. `.data/` is private local app data; production hosting must use a continuously running VM with persistent local storage or migrate the repository to a durable database before publishing.

## Development checks

```sh
uv run python -m scoutbot --check
uv run python -m unittest discover -s tests -v
```

The startup check opens and validates the SQLite schema and serializes all Discord slash commands without logging in or making changes in Discord.
