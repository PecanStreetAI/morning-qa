# Check 9 — Scheduled-job heartbeat health

_Last reviewed: 2026-08-27_

> **Pre-computed:** the `/admin/cron-health` payload is normally fetched by the
> pre-step into `/tmp/qa-precompute/bundle.md` (see SKILL.md § "Pre-computed
> inputs").  When this check's block is `OK`, use those facts — but the
> **Mongo cross-check gate for any stuck row you'd classify Critical stays YOURS**
> (the bundle never pre-runs Mongo).  The HTTP probe below is the FALLBACK for a
> `SKIPPED`/`ERROR`/absent block.

## Why this check exists

Before heartbeat tracking, the only signal that a scheduled job had
silently stopped firing was the *downstream consequence* days later:
"the weekly digest didn't go out this Monday", "the queue is empty on
Tuesday morning", "the Friday compose job never produced the draft the
operator was meant to review."

Heartbeat tracking flips the alarm: every successful scheduled-job run
writes a row to a `job_heartbeats` collection, and a missing row means
the job is silently broken.  This check is how that alarm surfaces in
the operator's morning report.

### The pattern this check assumes

Your app wraps each scheduled job so that a successful run upserts a
heartbeat row — `job_id`, `last_success_at`, `finished_at`, `success`,
plus a `meta` dict of the run's summary counters — and keeps a cadence
registry mapping each `job_id` to its expected cadence and an alarm
threshold (cadence + grace).  Example registry:

| job_id | Expected cadence | `threshold_hours` (cadence + grace) |
|---|---|---|
| `nightly_ingest` | 24h | 30 |
| `hourly_metrics_rollup` | 1h | 3 |
| `weekly_digest_dispatch` | 168h (Mondays 09:00 UTC) | 192 |

A `find_stuck_jobs()` helper compares each registered job's newest
heartbeat against its own threshold, and a small admin endpoint exposes
the result over HTTP so this check can run in CI without database
credentials.

## Steps

1. Fetch the stuck-job summary via the **HTTP endpoint** (built in
   production precisely to let this check run in CI without Mongo
   creds — see "Why HTTP probe AND Mongo cross-check" below):

   ```
   GET https://staging.example.com/admin/cron-health
   X-Admin-Key: $ADMIN_API_KEY
   ```

   Recommended response shape (pin yours with an endpoint test):

   ```json
   {
     "ok":            bool,
     "stuck_count":   int,
     "stuck":         [stuck-row dicts],
     "checked_at":    "2026-05-23T11:07:29Z",
     "expected_jobs": ["nightly_ingest", "hourly_metrics_rollup", "..."],
     "scheduler_instances":      1,
     "scheduler_instance_pids":  [4242],
     "scheduler_window_minutes": 15
   }
   ```

   Each stuck-row matches the shape `find_stuck_jobs()` returns
   in-process: `job_id`, `expected_cadence`, `last_success_at`,
   `hours_since`, `threshold_hours`, `never_ran`.

   **`scheduler_instances` — read this FIRST.**  When the scheduler
   runs in its own service unit, this field counts the distinct
   processes that wrote a scheduler heartbeat in the last
   `scheduler_window_minutes`.  **It must be `1`.**

   | Value | Meaning | Severity |
   |---|---|---|
   | `1` | Healthy | 🟢 |
   | `0` | No scheduler running anywhere — **every scheduled job is dead**, while the API still serves 200s and `/health` still returns 200. Triage: `systemctl status <your-scheduler-unit>`, then `journalctl -u <your-scheduler-unit> -n 100`. | 🔴 Critical |
   | `≥2` | Split-brain — duplicate ingestion + doubled outbound sends. Should be structurally impossible if you run one unit: suspect a re-added in-process scheduler or a second box on the same database. | 🔴 Critical |
   | `null` | The count query failed. **NOT the same as `0`.** | 🟡 Warning, do not alarm |

   This is the FAST detector: it reports `0` within ~one window of the
   scheduler dying, whereas the earliest stuck row (the scheduler's own
   heartbeat job, window + grace) lags it by an hour or more.  When
   `ok` is `false` with `stuck_count: 0`, some OTHER field in the
   response is the explanation — read the rest of the payload before
   reporting, and never report "all jobs fresh" from the stuck list
   alone.

   **A restart is not split-brain.**  Count only pids whose latest beat
   is within a couple of minutes of the newest beat, so a service
   restart handover (retired pid one full tick behind) reads as `1`.
   If you see `2`, the two processes really are beating together.

   **`never_ran`** distinguishes "registered but has not fired yet"
   from "used to fire and stopped".  A weekly job legitimately has no
   heartbeat for up to a week after ship — e.g. a job that fires only
   Saturday 03:00 UTC.  Compare the deploy age against that row's own
   `threshold_hours`, never a flat 48h.

   **Outcome (yield), not just freshness — the extension worth
   copying.**  A heartbeat marks success when the job *returns
   normally* — and a polling job that fails every item it polls still
   returns normally.  The production instance sat in exactly that
   blind spot for 6 days in 2026-08: 1,766 green heartbeats while a
   collector failed 100% of its polls (821 consecutive vendor 403s).
   The fix: for the highest-risk polling job, the endpoint also sums
   that job's `meta` counters across every completed pass in a
   rolling 24h window and flags **zero yield** — the window's passes
   ran and produced ONLY failures:

   | Window sums (last 24h of completed passes) | Meaning | Severity |
   |---|---|---|
   | `polled == 0`, `effective_failed == 0` | Idle window — nothing was due.  A measured 0 is a value, not a gap. | 🟢 healthy |
   | `polled > 0` | Items yielded — partial failure at worst. | 🟢 healthy here |
   | `polled == 0`, `effective_failed > 0` | Zero yield — only failures (flips `ok`). | 🟡 Warning (see Escalation under Severity) |
   | `null` counters | Nothing readable: no completed pass in the window (that is the staleness rows' business) or the read failed.  Does NOT flip `ok`. | 🟢 not an alarm |

   It is a WINDOW, not the latest pass, because failure backoff
   (interval doubling per consecutive error) makes most single passes
   read `polled=0, failed=0` during an outage — and one healthy pass
   would mask every failure beside it.  `effective_failed` = `failed`
   minus the benign skip categories the runner counts into it (e.g.
   `skipped_no_connection`, `skipped_needs_reconnect` — user-action
   states owned by a settings/reconnect surface, not collector
   defects).  Report the counters as counters only — see the ⚠️ on
   `meta` below.

   **Why HTTP probe AND Mongo cross-check, not one or the other**
   (production history, kept because the reasoning transfers):

   The HTTP probe is the PRIMARY uptime signal.  The first version of
   this check tried to import the app's DB layer and reach the hosted
   database directly from the CI runner; the connection hung past its
   timeout and the check was skipped every morning.  Plumbing a raw
   Mongo URI into CI as a workaround would have created a new
   exfiltration surface for a Tier-1 read-only agent with no
   credential boundary — so the `/admin/cron-health` endpoint was
   built instead, reusing the app's existing admin-key gate.  Net
   result: the HTTP probe tests *uptime + admin-auth boundary*
   end-to-end.  Keep this layer.

   (That "exfiltration surface" concern was correct *for a raw URI
   loaded into a CI runner*.  The MCP architecture closes the gap
   structurally: the URI lives in a GH Secret, the MCP server
   subprocess holds the credential, and the Tier-1 agent sees only the
   typed read-only tool surface.  So the historical reasoning is now
   superseded by a different threat model, not refuted.)

   With the read-only Mongo MCP in place, Mongo is the GROUND-TRUTH
   VERIFIER for what the HTTP probe reports: when the HTTP
   `stuck_count > 0`, the agent cross-checks the affected jobs in the
   `job_heartbeats` collection BEFORE emitting Critical.  See "Verify
   before classifying Critical" below.

   Both layers stay on purpose — they test different things:
   - HTTP probe: is the admin endpoint reachable + correct shape?
     Tests the app + admin-auth path.
   - Mongo cross-check: does the data layer actually show the
     stuck/fresh state the HTTP probe reported?  Tests the database
     directly, bypassing the admin endpoint.

2. For each `stuck` row returned:
   * If `last_success_at` is null → the job has never written a
     heartbeat.  Could be brand-new (deployed within the past cadence
     window) OR broken since deploy.  Surface either way.
   * Otherwise it has fired before but is past `cadence + grace`.
     Read `hours_since` and report as overdue.
3. Compute counts vs. yesterday's QA Issue if the prior report's
   check-9 section can be parsed; otherwise emit absolute counts.

## Severity

Read top-down; first match wins (SKILL.md § "Severity tables read
top-down, first match wins").

| Condition | Severity |
|---|---|
| Any job with `last_success_at = null` AND deploy age ≥ that job's own `threshold_hours` (`cadence + grace`) | 🔴 Critical |
| Any job with `hours_since > threshold_hours + 24h` | 🔴 Critical |
| Any job with `hours_since > threshold_hours` but ≤24h over | 🟡 Warning |
| Brand-new job (deploy age < its `threshold_hours`) with no heartbeat yet | 🟢 Info |
| Last-24h passes of a yield-tracked job sum to `polled == 0` AND `effective_failed > 0` — the passes ran and produced ONLY failures, net of benign skips; every heartbeat stays green the whole time | 🟡 Warning |
| All registered jobs fresh AND no zero-yield flag | _no finding_ |

**Escalation (dwell):** zero-yield 🟡 on two consecutive mornings —
today's flag AND yesterday's QA Issue's Check-9 section showing its
ZERO-YIELD line — treat as Critical.  (The 2026-08 production incident
ran 6 days; yesterday's value rides the verbatim issue body, so the
escalation needs no extra Mongo read.)

## Verify before classifying Critical

When the HTTP probe at `/admin/cron-health` returns a `stuck` row the
Severity table above classifies as 🔴 Critical (`last_success_at =
null` AND deploy ≥48h, OR `hours_since > threshold_hours + 24h`), the
agent MUST cross-check the `job_heartbeats` collection via the
`mongo-ro` MCP BEFORE emitting.  This gate enforces SKILL.md § "Mongo
cross-check gate for Critical findings" on the per-row level.

### Mongo query template

For each stuck row, fetch the heartbeat directly:

```
# database:   <your app database>
# collection: job_heartbeats
mcp__mongo-ro__find(
  filter     = {"job_id": "<the stuck job_id>"},
  projection = {
    "_id": 0,
    "last_success_at": 1,
    "expected_cadence_hours": 1,
    "threshold_hours": 1
  },
  limit      = 1
)
```

The projection limits the returned fields to what the gate needs, and
that projection is load-bearing rather than merely tidy:

⚠️ **Do not assume `meta` is free of sensitive text.**  If your
heartbeat wrapper folds a job's return value into `meta`, a job's
catch-all error path can embed things you must never paste into a
GitHub Issue — the production instance found a pymongo failure string
that embedded the Mongo URI *with its password*, and a cloud-SDK error
that carried account context.  The right fix is at the source (redact
+ cap error strings before they reach the heartbeat row), but the
report-side rule stands regardless: **do not paste raw `meta` values
into the report.**  Summarise them.  Keep the explicit projection — do
not widen it to a bare find.

### Mongo gate — the zero-yield Escalation (two consecutive mornings)

Before escalating a zero-yield 🟡 to Critical, mirror the endpoint's
window read directly (same counters-only discipline — the projection
below is the WHOLE allowed field set; never widen it):

```
# database:   <your app database>
# collection: job_heartbeats
mcp__mongo-ro__find(
  filter     = {"job_id": "<the yield-tracked job_id>", "success": true,
                "finished_at": {"$gte": <now minus 24h, ISO>}},
  projection = {"_id": 0, "finished_at": 1, "meta.polled": 1,
                "meta.failed": 1, "meta.skipped_no_connection": 1,
                "meta.skipped_needs_reconnect": 1},
  sort       = {"finished_at": -1},
  limit      = 60
)
```

Sum `polled`, `failed`, and the skip counters across the rows and
recompute `effective_failed = failed − skips`.  Escalate only when the
recompute agrees with the endpoint (`polled == 0` and
`effective_failed > 0`) AND yesterday's QA Issue's Check-9 section
shows the ZERO-YIELD line — yesterday's value rides the verbatim issue
body, so no second Mongo read is needed for it.  If the recompute
disagrees with the endpoint, report the divergence instead (the same
posture as the stuck-row decision rule below).

### Decision rule (per stuck row)

| HTTP vs Mongo | Decision |
|---|---|
| HTTP says stuck + Mongo confirms `last_success_at` is null or stale | Emit Critical as planned.  Add `(Mongo cross-check confirmed: last_success_at = <value or "null">)` to the row's note. |
| HTTP says stuck + Mongo shows `last_success_at` IS recent (within threshold) | DOWNGRADE the row to 🟢 Info with `"HTTP probe diverged from Mongo state for <job_id> — Mongo shows last_success_at = <ISO>, threshold_hours = N.  Possible admin endpoint cache / staleness.  Investigate /admin/cron-health before treating as a job outage."` |
| MCP unreachable | See SKILL.md § "Fallback when the Mongo MCP is unreachable" for the canonical handling (per-finding `[UNVERIFIED]` suffix + Info-tier meta-finding). |

The report's max severity is computed AFTER per-row downgrades, so a
wholly-divergent HTTP-vs-Mongo state results in Info-only output that
surfaces the admin endpoint divergence rather than crying wolf about
the jobs.

### Failure modes the gate catches

- **Admin endpoint cache staleness** — the `/admin/cron-health`
  response is hours old; Mongo shows a fresher heartbeat.
- **TZ-normalization bugs** in the admin route — `last_success_at`
  displayed wrong vs. the underlying value.
- **Recent recovery** — the job just succeeded; the HTTP response was
  generated before the new heartbeat landed.

When the gate confirms the HTTP probe (Mongo agrees), the job is
genuinely stuck; the gate adds confidence, not noise.

## Output format

```markdown
### Check 9 — Job heartbeats

Registered jobs: {N}  ·  Stuck: {S}  ·  Fresh: {F}
{if a yield-tracked job exists and its counters are non-null:}
Yield (24h): polled {P} · failed {Q} (effective {E}){ · ⚠️ ZERO YIELD when the flag is true}
{when they are null:}
Yield: no completed pass readable in the window

{when S > 0}
**Stuck jobs:**
| Job ID | Cadence | Last success | Overdue by |
|---|---|---|---|
| weekly_digest_dispatch | 7d | 2026-05-12T13:15Z | 252h (10.5d) |
| nightly_ingest         | 1d | never            | n/a          |

{when S == 0}
All {N} registered jobs fired successfully inside their
expected windows.
```

## Edge cases

* **Endpoint or Mongo unreachable from CI** — emit `🟡 Warning:
  heartbeat check could not reach its source; check skipped` and
  return.  Unavailable ≠ clean: a skipped check is reported as
  skipped, never as fresh.
* **Naive datetimes** — normalize to UTC-aware in the backend helper,
  not in this check.
* **Brand-new job added today** — the cadence registry got a new
  entry but the deploy hasn't fired the job once yet.  Defer the
  alarm by that job's own `threshold_hours` via the severity table
  above (never a flat cutoff — a weekly job shipped 3 days ago is
  healthy); classify as Info.

## How to fix (operator notes — the agent only reports)

If a job is stuck:

1. On the box, check the service log:
   `journalctl -u <your-app-or-scheduler-unit> -n 200 | grep <job_id>`.
2. If the scheduler is missing the job entirely, restart the
   scheduler service.
3. If the job is firing but failing, the exception should already be
   in your error tracker — fix the root cause + redeploy.
