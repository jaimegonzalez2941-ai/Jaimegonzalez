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

**How to apply:** Handle overwrite targets by known Discord type. Fail closed on unresolved
viewer grants and tell the manager to remove or verify the stale member override.
