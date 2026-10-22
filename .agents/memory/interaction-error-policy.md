---
name: Interaction error policy
description: User-requested actionable private errors across all Club Agent commands and controls.
---

The user asked to improve the bot's error messages for all commands, not just setup.

**Why:** Generic permission failures led to repeated mobile screenshots without identifying
the entry blocking setup.

**How to apply:** Keep error reports private, explain the known reason and a relevant next
action, and identify blocked permission entries with safe labels. Unexpected exceptions
need a log reference rather than raw internal details. Do not treat an interrupted response
as proof that a write failed, retry an action automatically, or weaken permissions to remove
an error. Expired reply tokens cannot be repaired by repeatedly sending the same error.
