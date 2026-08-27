# Check NN — {Name}

_Last reviewed: YYYY-MM-DD_

<!--
This is the check-spec template, distilled from the shape shared by the
shipped checks (00, 07, 09).  Copy it to `NN-your-check.md`, fill every
section, and add the check to SKILL.md's roster + report table (and to
the Mongo cross-check scope table, if it can emit a data-layer
Critical).  Delete the comments as you go.  The framework's
docs/check_catalog.md holds the one-line-per-check index — add a row
there too.

Keep the `Last reviewed:` line current — SKILL.md's quarterly
check-review cadence flags any check whose line is > 120 days old.
-->

> **Pre-computed:** {If the workflow's pre-compute script gathers this
> check's raw probes into `/tmp/qa-precompute/bundle.md`, say so here
> with the standard banner: when the bundle block is `OK`, use those
> facts and go straight to the severity rubric — the probe steps below
> are the FALLBACK for a `SKIPPED`/`ERROR`/absent block.  If the check
> is not pre-computed (its query shapes depend on intermediate
> results), say that instead.  Either way, be explicit: the agent must
> know whether re-running the probes wastes turns or is required.}

## Why this check exists

<!--
The WHY is load-bearing — it is what keeps a check from being deleted
as noise two quarters from now, and what calibrates the agent's
severity judgment.  Best shape: the dated incident or blind spot that
motivated the check ("before X, the only signal was the downstream
consequence days later: ...").  If the check pre-empts a failure that
hasn't happened yet, say what the failure would look like and why the
downstream symptom would be misleading.  Keep honest receipts —
measured numbers, dates, how long the blind spot lasted.
-->

## Inputs + pre-compute contract

<!--
Enumerate what the check reads and where each input comes from:
- bundle blocks (which MANIFEST tag gates them)
- HTTP endpoints (exact URL + required header env var — never the value)
- MCP verbs (which of the five allowed read-only verbs, which
  collections, which projections)
- files in the repo
State which env vars/secrets the check depends on and what happens
when each is unset (see Fallback below).  A check that needs a
credential the operator hasn't provisioned must self-skip with a 🟢
Info provisioning note, not fail the run.
-->

## Steps

<!--
Numbered, imperative, batched: combine independent shell probes into
one Bash call (every tool call is a full model round-trip).  Include
exact commands with a one-line gloss on anything non-obvious.  Mark
any step that is a security decision the agent must make itself even
when the bundle is OK (Check 7's allowlist re-verify is the model).
-->

## Severity

<!--
A table, ordered most-severe-first, evaluated TOP-DOWN,
FIRST-MATCH-WINS (SKILL.md § severity conventions).  Suppression rows
go ABOVE the rows they suppress.  Every condition must be mechanically
checkable from the inputs above — "seems unhealthy" is not a row.
End with the explicit no-finding row so "clean" is a defined state,
not an absence.
-->

| Condition | Severity |
|---|---|
| {worst condition} | 🔴 Critical |
| {degraded condition} | 🟡 Warning |
| {noteworthy but benign} | 🟢 Info |
| Clean | _no finding_ |

<!--
If any row can emit a data-layer Critical, state that the Mongo
cross-check gate applies and include the exact query template (filter
+ projection + limit) the gate should run — a tight projection is
load-bearing, not tidy (heartbeat/meta fields can carry text that must
never reach a GitHub Issue).

Dwell escalation (optional): if a Warning that persists should
escalate, name the window explicitly ("two consecutive mornings") and
say where yesterday's state comes from (normally the verbatim
yesterday-issue body from SKILL.md § "Before running" — prefer that
over an extra query).
-->

## Output format

<!--
The exact markdown block the check contributes to the report.  One
line for a clean day; tables only for adverse states.  Always-printed
fact lines (like Check 7's toolchain-pin lines) are deliberate —
passive visibility without a finding.
-->

```markdown
### Check NN — {Name}

{shape}
```

## Fallback when a source is unavailable

<!--
UNAVAILABLE ≠ CLEAN — the framework's core reporting rule.  For each
input, say what the check does when it cannot be read: typically a 🟡
Warning naming the unreachable source ("check skipped"), or a 🟢 Info
provisioning note for an optional credential.  Never let a failed
probe produce a ✅ row, and never backfill a missing fact with a
plausible adjacent one (see SKILL.md § Deploy state for the canonical
cautionary tale).  If a partial read is possible, report coverage
honestly ("N of M sources read").
-->

## Edge cases

<!--
The known benign twins of the alarm conditions: the restart that looks
like split-brain, the brand-new job that looks dead, the weekly job
that looks stale on a Wednesday.  Each edge case here is one
false-positive the operator never has to triage.
-->

## Maintenance notes

<!--
What will drift and when to re-baseline: assumptions about counts,
rosters, thresholds, external endpoints.  Who owns the remedy (this
check reads symptoms; runbooks own fixes).  Anything an operator must
update when the underlying system changes shape.
-->
