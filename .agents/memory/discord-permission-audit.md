---
name: Permission audits without member intent
description: How to verify exact Discord permission principals when member-list intent is disabled.
---

When reviewing saved Discord IDs or explicit channel-overwrite targets, use a single-member fetch for that exact ID. Do not treat `guild.get_member(id) == None` as proof that the member or bot is absent.

**Why:** Guild and channel fetches provide role and overwrite data, but a client without member-list intent may not cache unrelated members. A cache miss produced a false “missing source bot” result during an audit.

**How to apply:** Fetch only members referenced by saved configuration or relevant permission overrides. Distinguish a confirmed `NotFound` response from other API errors, and do not enable member-list intent just to check individual references.
