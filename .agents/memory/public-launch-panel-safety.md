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
