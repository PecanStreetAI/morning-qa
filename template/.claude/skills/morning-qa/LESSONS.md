# Lessons — morning-qa

Calibration log for the `morning-qa` skill.  When a report
mis-categorizes severity, misses a finding, or fires a
false-positive, the operator records it here.  The next run reads
this file (see `SKILL.md` § Lessons file) before today's pass, in
addition to yesterday's GH Issue — and when an entry here disagrees
with an in-skill default, the entry wins: it is newer and
operator-confirmed.

Note the write direction: the OPERATOR writes this file.  The Tier-1
agent may only *propose* an entry inside its report ("process note
flagged for capture") — it must never edit this file itself.

This log is the **process / calibration** complement to:
- Per-check sub-prompts (`checks/NN-*.md`) — what each check looks at.
- The `<!-- qa-max-severity: ... -->` marker convention — how
  severity is wire-transmitted.
- The < 5% Critical-FP-per-month target — the explicit goal this log
  lets us measure against (running tally: the framework's
  docs/calibration_ledger.md).

## Format

```
- YYYY-MM-DD — Issue #N, check NN.
  Skill said: <finding + severity>.
  Right answer: <correct severity / wording / not-a-finding>.
  Why: <one-line root cause — threshold drift / structural
       quirk / stale assumption / scope creep / etc.>
```

Add the most recent at the top.  Archive oldest 10 to
`LESSONS_archive.md` when this file passes ~30 entries.

## Scope

Things this log captures:
- Severity miscalls (Critical → Warning, or vice-versa).
- False-positive patterns the skill should learn to recognize.
- False-negative patterns (the check missed something the operator
  caught manually).
- Tier-1 violations (the skill started doing a Tier-2 thing).
- Structural quirks (e.g. severity derived from a report header
  that is always present).

Things this log does NOT capture (live elsewhere):
- Per-check implementation details → `checks/NN-*.md`.
- Promotion criteria → the framework's docs/promotion_criteria.md.
- Incident postmortems → your repo's postmortem directory.

## Lessons

_(none yet — entries accrue as the operator corrects real runs)_
