# FC Scouting & Performance Bot V2

A continuously running Discord bot for private player scouting, recruitment roles, and trusted match recaps. The original bot script and any earlier database have not been changed. V2 starts with its own database at `.data/scout-v2.db`.

## Start the bot

- `DISCORD_TOKEN` must be saved as a Replit Secret. Do not put it in source, configuration, or Discord messages.
- For full automatic recap reading, open the **Bot** page of this bot's application in the [Discord Developer Portal](https://discord.com/developers/applications) and enable **Message Content Intent**. Grant this bot Message Content Intent as a privileged gateway intent, then restart it. Without this, Discord rejects the bot's gateway connection.
- Click **Run** for the **Scout Discord Bot** workflow. When it connects, the workflow log prints a bot-invite link. Open that link to add V2 to your server with the requested channel, message, embed, history, and role permissions.
- `/scout` commands sync globally and may take Discord a little time to appear. Set `DISCORD_TEST_GUILD_ID` before startup to sync to one server immediately while testing.
- In Discord, a server manager runs `/scout setup`: choose a dedicated staff role, a staff-only channel, the channel that receives recaps, and either a trusted bot in your server or the exact numeric ID of a webhook. Setup rejects channels that explicitly allow non-staff viewers.
- Add recruitment roles to candidates only after a manager maps each stage using `/scout stage_role`. The bot adds the chosen role and deliberately does not remove any existing roles.
- Scouts map player accounts with `/scout link`. Then use `/scout evaluate`, `/scout history`, `/scout stats`, or `/scout docket`.

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
