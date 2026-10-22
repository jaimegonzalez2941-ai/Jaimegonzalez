---
name: Public launch-panel safety
description: Why welcome-panel publication uses conservative recovery rather than blind retries.
---

Prefer a clearly blocked publication or move over creating a second public launch panel.
An inaccessible old channel must not be treated as a deleted one.

**Why:** The requested launch experience is one manager-selected panel per server. Discord
publication and local persistence cannot commit atomically; a lost send reply or restart
does not prove that nothing was posted.

**How to apply:** Preserve durable publication intent and reconcile uncertain sends before
retrying. Do not replace inaccessible messages merely because the old location cannot be read.
Public launch controls are generic entry points, never authority inherited from their publisher.

After slash-command registration changes, the live bot and Discord client can have different
command trees until the bot completes an online sync. A `CommandNotFound` is not evidence of a
panel-channel permission problem.

**Why:** `/start_panel` was selectable in Discord, but the running bot had no registered handler.
Restarting the bot synced the command tree; the user confirmed that the command then worked.

**How to apply:** When a command produces `CommandNotFound` after code has registered it, verify
the live sync state before debugging command logic, channel permissions or panel publication.

Discord can return an unresolved `Object` for a user-specific channel overwrite when that member
is not cached. It is neither a verified member nor a verified staff principal.

**Why:** `/scout setup` reached the staff-channel privacy check and raised `AttributeError` when an
uncached overwrite target was treated like a `Member`.

**How to apply:** Handle overwrite targets by known Discord type. An uncached member may be
legitimate staff: verify that individual against Discord within a bounded request budget before
deciding. A missing role cannot be resolved as a member. Keep unknown or unauthorized viewers
blocked, and acknowledge the interaction before any network verification.

Discord's overwrite API uses the generic `discord.abc.User` protocol as the unresolved member
type, not necessarily the concrete `discord.User` class.

**Why:** Checking only concrete User/Member types silently skipped verification for actual
Discord channel payloads, even though manually constructed test objects passed.

**How to apply:** Include the protocol type explicitly; do not use `issubclass` against this
data-bearing protocol. Exercise real SDK channel payload parsing in regression tests rather than
relying only on hand-built overwrite objects.

Club Agent's own Discord-managed bot role is a safe viewer only when authoritative Discord role
tags identify Club Agent in the same server.

**Why:** Giving the bot access through its own managed role should not make a private staff
channel fail validation or force a manager to grant Administrator.

**How to apply:** Do not infer exclusivity from role names or from a cached list of role members;
ordinary shared roles and another bot's managed role still need the normal staff authorization.

Permission-validation failures should identify the blocked permission entry and the selected
staff role with bounded, mention-safe labels, not just say that an unspecified viewer is unsafe.

**Why:** Repeated mobile screenshots did not expose the relevant permission, leaving the manager
unable to identify the rule from a generic error.

**How to apply:** Preserve fail-closed authorization while making private validation messages
specific enough to act on; never infer a viewer's identity or safety from a role name alone.

When diagnosing source setup, verify the live account and current per-match report destination rather
than treating a historical bot display name or leaderboard link as authoritative.

**Why:** A bot shown under an older name in screenshots had since been renamed; its current match
reports and its leaderboard also appeared in different channels. An unresolved permission label was
not evidence that the account lacked manager access.

**How to apply:** Use verified account IDs and a small bounded sample of actual report metadata.
Check current permissions before recommending removal of any viewer grant; do not guess the source
channel from the location of a leaderboard.
