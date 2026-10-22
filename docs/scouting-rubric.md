# Staff scouting rubric — `staff-v1`

This is an original, documented scouting rubric, **not a trained or statistically
validated predictor**. Its weights are adjustable design defaults, not empirical
findings. Calibration against real staff outcomes is future work.

## Four distinct concepts

1. **EA match rating:** imported as reported by a configured trusted recap source.
2. **Human category scores:** staff observations of the assigned role.
3. **Weighted scouting rating:** deterministic arithmetic on those five scores.
4. **Recruitment stage:** a separate staff decision, changed only through the
   existing explicitly authorized `/scout assign` command.

Trial-ready means eligible for staff consideration, not permission to assign a
role. No evaluation button changes Discord roles. Goals/assists cannot establish
saves, interceptions, minutes, per-90 rates, tactical behavior or attitude. Only
actually observed actions support human category evidence.

## Weights and observable duties

The order is always role execution / decision-making / positioning / ball use /
teamwork. All rows total 100%.

| Position | Role execution | Decision-making | Positioning | Ball use | Teamwork |
|---|---:|---:|---:|---:|---:|
| Goalkeeper | 35% | 20% | 20% | 15% | 10% |
| Defender | 30% | 20% | 25% | 15% | 10% |
| Midfielder | 20% | 25% | 20% | 25% | 10% |
| Attacker | 35% | 20% | 20% | 15% | 10% |

| Position | Role execution | Decision-making | Positioning | Ball use | Teamwork |
|---|---|---|---|---|---|
| Goalkeeper | Shot handling, parries, crosses | Hold/release/claim/stay choice and timing | Starting depth, angles, recovery | First touch, distribution accuracy, direction, pace | Visible coordination, back-pass availability |
| Defender | Marking, challenges, defensive duels, avoiding needless fouls | Engage/delay/clear/retain choice | Line discipline, cover, runners, recovery shape | Touch and passing to retain/progress | Cover and coordinated movement |
| Midfielder | Connect phases, support transitions, fulfill assigned role | Tempo/pass/carry/defensive choice | Receiving angles, spacing, scanning movement, coverage | Touch, pass execution, retention/progression | Combinations, cover, visible coordination |
| Attacker | Finishing, chance creation, assigned attacking duties | Shoot/pass/carry/press choice | Run timing, space creation, onside discipline, pressing angles | Touch, combinations, dribbling, shot execution | Link play, coordinated pressing, supporting runs |

### Anchors for every role/category

Apply the following anchors **to that cell's observable duties**, not to overall
player reputation. For example, goalkeeper role-execution 4 means inconsistent
handling/cross execution requiring substantial support, whereas attacker
positioning 8 means strong timing/space/onside execution under observed pressure.
Do not penalize a goalkeeper for having no goals or infer teamwork from assists.

| Score | Observed execution of the specified duty |
|---|---|
| 1 | Repeatedly fails the observed duty |
| 2 | Rare successful execution |
| 3 | Frequent errors |
| 4 | Inconsistent; substantial support needed |
| 5 | Adequate on simple actions |
| 6 | Usually reliable; some pressure errors |
| 7 | Consistently effective in the observed context |
| 8 | Strong under pressure |
| 9 | Repeatedly exceptional against observed opposition |
| 10 | Exceptional throughout the observed sample, no material lapse |

Category scores accept 1–10 with at most one decimal place; decimals interpolate
adjacent anchors. Category scores **may** be 6.8–7.2: the no-7 policy applies to
the **overall result**, not individual observations or EA ratings.
Blank categories remain unassessed. Each assessed category requires 3–500
characters of evidence. All five must be assessed; no zero imputation or silent
reweighting. Summary notes (3–1000 characters) and observation context (3–500
characters, e.g. dates, opposition, mode and assigned role) are required to save.

## Arithmetic, boundaries and recommendation

Use exact decimal arithmetic:

`raw = sum(category_score × percentage / 100)`

Round once, at the end, to one decimal using **ROUND_HALF_UP** (8.05 → 8.1,
7.95 → 8.0). The review shows raw total, rounded rating, weights and individual
contributions. Human inputs remain unchanged.

If **either the exact raw total or rounded rating is in 6.8–7.2 inclusive**, the
draft is borderline and cannot be published. This conservative rule also prevents
6.76 → 6.8 or 7.24 → 7.2 from bypassing the excluded band. Never snap to 6.7/7.3
or edit an overall score directly. Reassess genuine underlying observations or
discard; do not adjust evidence merely to obtain a preferred result.

A completed rating of **8.0 or higher** recommends “Trial-ready for staff
consideration”; lower permitted ratings recommend “Developmental”. Coverage is
shown separately in both cases and must inform the human decision.

## Coverage is not confidence

Staff declare **1–10000 observed matches/sessions** (whole positive count):

- 1–2: **Limited**
- 3–4: **Developing**
- 5+: **Broader**

These are declared evidence-coverage labels, **not verified statistical
confidence**. A 9.0 from one session is still limited coverage. The **Trusted match
context** button separately displays the count of verified imported player
records, average EA rating and only reported metrics, with source links. Those
records establish trusted-source import provenance, not independent verification
of EA data or equality to the declared observation count. Several linked handles
can produce multiple player records for one recap. None enter the score formula.

## Worked examples

For scores **9 / 8 / 8 / 7 / 9** in the stated category order:

| Position | Raw calculation | Final | Recommendation |
|---|---|---|---|
| Goalkeeper | 3.15 + 1.60 + 1.60 + 1.05 + 0.90 = 8.30 | 8.3 | Trial-ready for staff consideration |
| Defender | 2.70 + 1.60 + 2.00 + 1.05 + 0.90 = 8.25 | 8.3 | Trial-ready for staff consideration |
| Midfielder | 1.80 + 2.00 + 1.60 + 1.75 + 0.90 = 8.05 | 8.1 | Trial-ready for staff consideration |
| Attacker | 3.15 + 1.60 + 1.60 + 1.05 + 0.90 = 8.30 | 8.3 | Trial-ready for staff consideration |

With one declared session, **each remains Limited coverage**, even if 12 imported
records are available. With five declared sessions it has Broader coverage; this
does not change its performance rating or grant a recruitment role.

- All categories 7.0 → raw/final 7.0 → borderline draft, not trial-ready.
- All categories 7.3 → 7.3 → Developmental, if evidence/context are complete.
- All categories 7.9 → 7.9 → Developmental.
- Four categories 7.9 and teamwork 8.4 → raw 7.95 → 8.0 → trial consideration.
- Any category blank → no final rating, regardless of the other scores.
- All categories 10 with zero declared observations → cannot save.

## Private workflow and persistence

1. `/scout evaluate candidate` → select a position. This resumes that scout's
   existing open draft for this candidate/position in this server, or creates one.
2. Enter **Context / coverage**. Select each category to read its role-specific
   guidance and anchors, then enter/correct its score and observed evidence.
3. Review the whole breakdown and optional trusted match context. Corrections
   persist immediately as private draft changes, not completed evaluations.
4. **Confirm & save** explicitly publishes a complete, non-borderline evaluation
   to staff history/docket. Repeated submissions return the same record.
5. **Discard draft** closes only the draft; it does not delete evaluations or
   imported statistics. Timed-out controls can be resumed by repeating step 1.

Staff access, draft owner and server are rechecked at every scorecard interaction.
Revision checks reject stale forms rather than overwriting newer observations.
Draft creation, corrections (before/after), saving and discard are transactionally
audited. Completed records are read-only. Drafts survive restarts; Discord controls
time out and are recreated by reopening the command.

The SQLite upgrade is additive: scorecard metadata columns and a separate draft
table. Existing evaluation IDs, scores, notes, match records, and role mappings
remain intact. Old scores are labeled **Legacy single-score entry**, with no
invented category values, declared coverage or retrospective recalculation.

### Versioning rule

`staff-v1` is immutable. Any future weight, anchor, formula, boundary or rounding
change must introduce a new rubric version and retain the old version's renderer
and calculator. Never modify this version in place or recalculate saved records.
Persisted category values, assessor, position, context, notes and version explain
each saved rating independently of future calibration.

## Connected staff evidence views

`/scout player member` paginates the full saved category observations, context and
notes rather than the abbreviated history summaries. It includes saved dates and
rubric versions; saved timestamps are not observation dates. Structured observation
dates were not collected; any dates in context remain staff declarations.
Other scouts' private drafts are excluded.

`/scout compare first second position` selects the latest saved evaluation at the
chosen position for each player. Numeric category comparisons require the same
supported rubric version on both records. Missing, legacy or incompatible records
are labeled, with no invented subscores, retrospective recalculation, overall winner
or statistical confidence. Full saved evidence stays readable.

Imported EA metrics remain a separately labeled source with individual reported-row
sample sizes. Declared coverage is not an imported match count; imported rows need
not represent unique matches or independent observations. Neither source proves
potential, personality or improvement. Recruitment reviews, development completion
and staff tasks are tracking records only and never alter this rubric or its scores.
