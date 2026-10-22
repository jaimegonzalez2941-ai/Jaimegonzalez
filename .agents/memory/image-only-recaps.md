---
name: Image-only recap policy
description: Product rule for recaps where individual player statistics are present only in leaderboard images.
---

Do not import player statistics from leaderboard images. Only explicit text rows in message content or embed descriptions/fields are trusted; image-only recap posts remain unimported.

**Why:** The Friday recap source puts per-player statistics in a graphic, where automated extraction can misread values and create inaccurate records.

**How to apply:** Keep missing values unreported and reject image-only posts rather than guessing or adding OCR unless the user explicitly changes this decision.
