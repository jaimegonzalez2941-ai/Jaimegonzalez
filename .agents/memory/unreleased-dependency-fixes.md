---
name: Unreleased dependency fixes
description: Handling vulnerability findings when upstream fixes exist only as unreleased patches.
---

An upstream Git patch can contain a working security fix while retaining a version that dependency scanners still flag. Confirm actual protection and package identity separately; do not suppress an advisory or invent a version to clear a scan.

**Why:** The braces depth-guard patch retained the vulnerable upstream identity, so the dependency audit continued to report stack exhaustion even with patched source.

**How to apply:** When no official fixed release exists, inspect a published backport's implementation before adopting it, pin it exactly, test malicious inputs and normal behavior through actual consumers, and rerun the dependency audit. Prefer an official compatible fix once available.
