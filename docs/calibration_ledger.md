# Calibration ledger

The promotion gates in [promotion_criteria.md](promotion_criteria.md) are
quantitative: **Tier 1 → Tier 2** needs **≥ 14 consecutive clean days** (no
operator-confirmed false-positive Critical) AND **≥ 3 operator-confirmed
true-positive findings**. Those counters need somewhere to live — this file is
it. Without a ledger the gates can't be evaluated, so promotion stalls
indefinitely.

**Who keeps it: the operator.** The operator appends a row when they confirm a
finding was real — or a false alarm. The weekly retro bookkeeper
(`template/.github/workflows/qa-ledger-retro.yml`, Mondays) reads the week's
QA Issues plus the operator's `TP`/`FP` comments and **proposes** rows in a
`[qa-ledger]` sign-off issue; it never edits this file — the operator pastes
approved rows here. **The agent never self-certifies its own findings as true
positives** — that would defeat the gate. This is the load-bearing rule of the
whole ladder: the entity whose autonomy is being measured does not hold the
pen on the measurement.

**What counts:**

- **True positive (TP):** a finding the operator confirms was a real issue
  worth surfacing — any severity. Counts toward Tier-2 gate #2 (≥ 3).
- **False positive (FP):** a finding — especially a Critical — the operator
  confirms was noise. A **Critical FP resets the 14-clean-day clock**
  (gate #1) and counts against the Critical-FP-rate budget in the skill's
  false-positive target. A Warning- or Info-severity FP is recorded but does
  **not** reset the clock.
- Routine "all clean" days are **not** ledger rows — only confirmed TP/FP
  judgments are. The streak is computed from the calendar and the FP rows, not
  from a row per day.

**An open, un-verdicted Critical is not a clean day — it is an unmeasured
one.** The production instance learned this the hard way in 2026-08: its tally
read "gate #1 unbroken" while two Critical findings sat open with no verdict.
When the operator finally adjudicated them, both were FPs — and the clock reset
to a date two weeks in the past. Confirming a streak without ruling on every
open Critical certifies a number no one has checked. Before ever confirming
gate #1, search for open issues carrying both the daily label and
`priority:critical`; the daily supersede step deliberately never closes them,
so they accumulate silently rather than surfacing as a blocker.

## Tier-1 → Tier-2 progress (live tally)

- **Clean-day streak (no Critical FP):** clock starts the day you begin
  running at Tier 1. *(not started)*
- **Confirmed true positives toward the ≥ 3 gate:** **0**
- **Earliest possible Tier-2 promotion date:** 14 days from the clock start,
  if no Critical FP lands in between and gate #2 is met.

## Ledger

| Date | QA Issue | Check | Finding (1-line) | Verdict | Notes |
|---|---|---|---|---|---|
| | | | | | |

### Example rows (illustrative only — rewritten from the production instance's history; delete this section when you adopt)

| Date | QA Issue | Check | Finding (1-line) | Verdict | Notes |
|---|---|---|---|---|---|
| 2026-07-22 | #NNN | Check 16 | First Critical fire — a scale-discontinuity quorum: six cities in one metro showing ~2.6–3.5× year-over-year jumps on the same flow metric at once, feeding user-visible cards | TP | Operator-confirmed with a verdict comment on the day's issue: TP on detection, wrong suspected cause — the inflation was real bad data, but an upstream vendor artifact, not the residual internal bug the report suspected. A TP row can (and should) record that the agent's *diagnosis* was wrong even when its *detection* was right. |
| 2026-08-17 | #NNN | Check 16 | The same already-expired card escalated to Critical on its 2nd consecutive day — but both the cited value and the raw value were unchanged from the prior report: a re-observation of a static measurement, not drift | FP | **Critical-severity ⇒ resets the 14-clean-day clock (gate #1).** Root cause was a persistence clause counting days-of-looking rather than days-of-drift; fixed by narrowing the clause. The agent itself had proposed this remedy in the report — an FP row can still credit good remediation thinking. |

### How to add a row

When you (the operator) confirm a finding, append a row:

```
| YYYY-MM-DD | #NNN | Check K | <one-line> | TP / FP | <why> |
```

then update the live tally above:

- **TP** → increment the true-positive count.
- **Critical FP** → reset the clean-day streak to that date, and note it in
  [promotion_criteria.md](promotion_criteria.md) § Demotion log if it caused a
  revert or downtime.

The fastest way to confirm: comment the verdict directly on the day's
`[qa-agent]` GitHub Issue. The retro recognizes two comment shapes: a body
that **starts** with `TP` or `FP` (plus a one-line reason), or a
`verdict: TP|FP|TRUE POSITIVE|FALSE POSITIVE` line inside a longer comment.
Bare "TP" mid-prose never counts, and bot comments never count — those are the
anti-self-certification guards. **One comment may carry several verdicts** —
"`TP — Check 15: …. TP — Check 2: …`" proposes two rows, one per check.
Separate them with a sentence break or a newline, and give each token its own
`—`/`:` separator, so a passing mention of "TP/FP" in prose is not read as a
second verdict. (This multi-verdict shape is load-bearing: an early version of
the production retro kept only the FIRST verdict in a comment and silently
merged the rest into that row's Finding cell — undercounting the TP tally at
exactly the moment gate #2 depended on it. Fixed 2026-08; the parser now lives
in `template/.github/scripts/qa_ledger_retro.py` where it is unit-testable.)

Monday's retro run turns those comments into proposed rows in its
`[qa-ledger]` sign-off issue for you to paste here: they render in this
table's exact column order (paste verbatim), and any `(QA Issue, Check)` pair
already in the table is listed as "already in the ledger — do not paste"
instead of re-proposed, so one finding can't double-count toward the ≥ 3-TP
gate.
