# Check catalog

The checks the agent runs every morning, with their severity rubrics. This
repo ships three worked examples — Check 0 (disk vitals), Check 7
(dependency + security), and Check 9 (cron heartbeats) — plus the spec
template for writing your own. The production instance this framework was
extracted from runs 17 checks in the same format daily.

## Catalog format

Each check gets one section here and one spec file under
`template/.claude/skills/morning-qa/checks/` (so the skill's `SKILL.md` stays
readable and each check can be tuned independently). A catalog row carries:

* **What it does** — purpose + inputs (endpoints, commands, files), and a
  short steps summary. The spec file holds the full step-by-step prompt.
* **Why it exists** — the incident or blind spot that motivated it, dated.
  A check without a "why" is a check nobody can retune later.
* **Signals** — the severity rubric, a table evaluated **top-down,
  first-match-wins**. Ordering is part of the rubric: put the
  "check is blind" row first so an unavailable input can never fall through
  to a clean row.
* **Fallbacks / edge cases** — what the check does when its input is missing
  (self-skip severity, never a crash) and any escalation rules (e.g. "this 🟡
  on two consecutive mornings → treat as Critical").

Checks execute in numbered order, sorted by signal-to-noise: a hard fail on
an early check can short-circuit the run (if the pre-flight says the disk is
full, downstream checks are expected to fail and the agent should say so
without burning tokens on them).

**Precompute:** the mechanical data-gathering for the shipped checks (0, 7,
9) runs once in a deterministic pre-step — see
[precompute.md](precompute.md). The agent reads one bundle and applies the
severity rubrics below instead of re-running those probes. The rubrics here
are unchanged by that: precompute moved *where the raw data comes from*, not
*who judges it*.

**Lockstep:** the check specs in `template/.claude/skills/morning-qa/checks/`
and this catalog are kept in lockstep by the tests in `tests/` — thresholds,
allowlist entries, and tables that appear in both places are pinned so a
one-sided edit fails CI at PR time instead of surfacing as doc drift weeks
later.

---

## Check 0 — Disk vitals (pre-flight)

**What it does:** `df -P -k / /tmp` on the runner; parses the root `/`
used-%. Runs FIRST so a full disk is caught before it crash-loops the
downstream checks (a 2026-06 incident in the production instance: a filling
volume on the runner's host degraded every check that wrote temp files, and
the reports blamed the checks). Full spec:
`template/.claude/skills/morning-qa/checks/00-disk-vitals.md`.

**Signals:**

| Condition (root `/` used %) | Severity |
|---|---|
| ≥ 95% | 🔴 Critical — short-circuits the run |
| 80–94% | 🟡 Warning |
| < 80% | 🟢 Info (report the % for trend) |

---

## Check 7 — Dependency + security

**What it does:**

* `npm outdated --json` → filter to majors only.
* `pip list --outdated --format=json` → same filter.
* `npm audit --audit-level=high --json`
* `pip-audit --strict` (or equivalent)
* Cross-check GitHub Security Advisories for pinned versions in the
  lockfiles.
* **Step 6 — the CI-toolchain watch:** reads the exact versions of the
  globally-installed CI tools (the Claude Code CLI and the Mongo MCP server)
  straight from the workflow's own `npm install -g` lines — so the watcher
  can never drift from the pins it checks — and reports, per tool, the
  `latest`/`stable` dist-tags, days- and stable-releases-behind, and any
  advisory affecting the **pinned** version. These global installs are
  invisible to Dependabot (no manifest), so this check is their only
  refresher signal. Rationale: [design.md](design.md) § Toolchain pinning.

**Signals:**

| Condition | Severity |
|---|---|
| CVE on the accepted-CVE allowlist (documented not-reachable / deferred) | _suppressed → "Accepted (N)"_ |
| New HIGH or CRITICAL CVE matching a pinned version (not allowlisted) | 🔴 Critical |
| Major version available for a security-sensitive dep (crypto, web framework, UI framework) | 🟡 Warning |
| CI-tool pin >30 days behind latest, or an advisory on a pinned CI tool | 🟡 Warning |
| Major bumps available for other deps · CI-tool pin drifting (<30 days) | 🟢 Info |

**Accepted-CVE allowlist:** documented not-reachable or
deliberately-deferred CVEs are suppressed to a terse "Accepted (N)" tally
rather than re-flagged daily — and can't flip to a false Critical under the
literal "HIGH on a pinned dep" rule. The list, with a per-entry removal
condition and the greps the agent re-runs to verify the entry still holds,
lives in `template/.claude/skills/morning-qa/checks/07-deps-security.md`.
Entry shape (illustrative):

> `CVE-YYYY-NNNNN` (`somelib`, transport-level, not reachable — we only use
> the stdio path; re-verify: `grep -r "somelib.http" backend/` returns
> nothing; remove when the pinned version's major is bumped)

Any advisory id this catalog cites must also appear in the check file's
allowlist — the tests in `tests/` enforce that direction (the shipped catalog
carries no real ids, so start the lockstep from the check file when you add
your first entry).

**CI-tool advisories are Warning, not Critical:** these installers are
dev/CI-only, never shipped to a user. Bumping stays the deliberate human step
(the BUMP LOG discipline in [design.md](design.md)).

**Note:** keep this check brief. The agent is NOT a CVE database — it reports
what its tools tell it. Manual bumps remain the operator's call.

---

## Check 9 — Cron heartbeat health

**What it does:** calls `GET /admin/cron-health` on the monitored app (gated
by an `X-Admin-Key` header from the `ADMIN_API_KEY` secret). The endpoint
returns the app's own stuck-job computation — `ok`, `stuck_count`, `stuck`
(list), `checked_at`, `expected_jobs` — surfacing any scheduled job whose
most recent successful heartbeat is older than its cadence + grace. The
pattern: every successful job run writes a heartbeat row (e.g. to a
`job_heartbeats` collection), a cadence registry in the app declares each
job's expected interval + grace, and the endpoint diffs the two. Full spec:
`template/.claude/skills/morning-qa/checks/09-cron-heartbeats.md`.

**Why it exists:** before heartbeat tracking (2026-05 in the production
instance), a stuck cron — the weekly newsletter compose, the daily queue
populator — only revealed itself via downstream consequence days later: "the
newsletter didn't go out," "the queue is empty Tuesday morning." The
scheduler itself was opaque. Heartbeat tracking flips the alarm: a missing
row means the job is silently broken, and the morning report says so the next
day instead of whenever a human notices the consequence.

**Registered jobs** (illustrative — your app's cadence registry is the source
of truth; keep the table in your catalog mirrored from it, and pin the mirror
with a test so a missed sync fails CI at PR time):

| Job ID | Cadence | Grace |
|---|---|---|
| `newsletter_dispatch_weekly` | 7d | 6h |
| `daily_data_ingest` | 1d | 6h |
| `queue_runner` | 15m | 1h |
| `metrics_poller` | 1h | 1h |
| `weekly_report_compose` | 7d | 12h |
| `scheduler_heartbeat` | 15m | 1h |

Two hard-won registry rules from the production instance:

* **Register liveness beats for every job, including ones that "can't
  fail."** Six of that instance's ingest jobs recorded no heartbeat at all
  for their first months, so the stuck-job check structurally could not see
  the entire data spine stop. That is what turned a worker-election bug in
  the app-server config into a 24–30-hour-to-detect total ingestion outage:
  any worker respawn left no process holding the scheduler, every cron died,
  and the app kept serving 200s with `/health` reporting "degraded," not an
  error status. A liveness beat means "the scheduler fired this," not "the
  job produced good data" — keep data-quality checks separate, so a dark
  upstream feed can't paint this check permanently red.
* **`scheduler_heartbeat` is the process-liveness beat:** it fires every few
  minutes, cannot fail, and stamps the writing process's pid. Its short
  window alarms ~75 minutes after the scheduler process dies. A job that
  runs *chained inside* other host jobs (not as its own scheduler entry)
  needs a registry comment saying so — when it goes stuck, triage the host
  jobs first; "the scheduler is missing the job" does not apply to it.

**`scheduler_instances` — the split-brain / no-brain field:** the same
response carries `scheduler_instances` (plus the pids and the window used):
the count of DISTINCT processes that wrote a `scheduler_heartbeat` in the
last window. **The invariant is exactly 1.**

| Value | Meaning | Action |
|---|---|---|
| `1` | Healthy. | — |
| `0` | No scheduler is running anywhere. Every cron job is dead; the API is still serving 200s. | 🔴 Critical. Check the scheduler service's status and logs on the host. |
| `≥2` | Split-brain — two schedulers running the same crons: duplicate ingestion, doubled outbound sends. | 🔴 Critical. Should be structurally impossible (one service unit; the app refuses in-process scheduling outside development), so suspect a re-added in-process gate or a second host pointed at the same database. |
| `null` | The count query itself failed. NOT the same as `0`. | 🟡 Warning; do not alarm. |

Read this *alongside* the `scheduler_heartbeat` row, not instead of it: they
are the same fact on different clocks — `scheduler_instances` goes to `0`
within minutes, the stuck-job row appears at ~75 minutes.

**Zero-yield — the green-heartbeat trap (added 2026-08-26 in the production
instance):** for polling jobs, the response can also carry an OUTCOME block —
`zero_yield`, `polled`, `failed`, `effective_failed`, `window_hours`,
`finished_at` — summing the counters of every completed pass in the last 24
hours. `zero_yield: true` means the window had `polled == 0` AND
`effective_failed > 0`: **the passes ran and produced ONLY failures.** This
is the counter-example to "green heartbeat = healthy": a heartbeat wrapper
marks success when the wrapped pass returns normally, and a pass that fails
every item still returns normally. The motivating incident: a social-metrics
collector shipped and collected **nothing** for six days — 1,766 green
heartbeats over 821 consecutive vendor 403s — while every freshness row on
this check stayed green the whole time. **A green heartbeat measures the job,
not the work; something must read the yield.**

Design details that make the yield reader honest:

* **A 24h WINDOW, not the latest pass:** once per-row failure backoff exists
  (a failing row's poll interval doubling per consecutive error), most
  individual passes during a total outage find nothing due and read
  `polled=0, failed=0` — a latest-pass read is mostly blind, and any single
  healthy poll would mask every failure beside it.
* **`effective_failed` subtracts benign skip categories** — user-action
  states (a disconnected account, a token waiting on re-consent) that have
  their own surfaces and are not collector defects. Without the subtraction,
  one disconnected user with a pending row holds the flag up forever.
* **A measured 0 is a value:** `polled == 0, effective_failed == 0` is an
  IDLE window (nothing was due) — healthy. `polled > 0` means items yielded —
  partial failure at worst. `null` means nothing readable — no completed pass
  in the window, or the read failed — and, like a null
  `scheduler_instances`, it never flips `ok` and is not an alarm.

**Signals:**

| Condition | Severity |
|---|---|
| Endpoint unreachable / 5xx / auth failure — "cron-health unreachable; check skipped" | 🟡 Warning (never a silent clean) |
| `scheduler_instances` is `0` or `≥2` | 🔴 Critical |
| Job >24h past `cadence + grace` | 🔴 Critical |
| `never_ran: true` AND deploy older than that job's `cadence + grace` | 🔴 Critical |
| Job 0–24h over `cadence + grace` | 🟡 Warning |
| `never_ran: true` AND deploy younger than that job's `cadence + grace` | 🟢 Info |
| `scheduler_instances` is `null` (count query failed) | 🟡 Warning |
| A polling job's 24h window sums to `polled == 0` AND `effective_failed > 0` (`zero_yield: true`) — the passes ran and produced only failures, net of benign skips; every heartbeat stays green the whole time | 🟡 Warning |
| All registered crons fresh AND `scheduler_instances == 1` AND no `zero_yield` | 🟢 Info |

**Escalation:** zero-yield 🟡 on two consecutive mornings — today's
`zero_yield: true` AND yesterday's QA issue's Check-9 section showing its
zero-yield line — treat as Critical. (The motivating outage ran six days;
yesterday's value rides the verbatim issue body, so the escalation needs no
extra database read.)

**The never-ran rule is per-job, not a flat cutoff.** Stuck rows carry
`never_ran: true|false`. A flat "Critical once the deploy is ≥48h old with no
heartbeat" rule is wrong for any job whose cadence exceeds the cutoff: a
weekly job that fires only Saturday, shipped on a Sunday, is legitimately
never-ran for six days — the flat rule would file a 🔴 every one of those
mornings. Compare deploy age against **that job's own `cadence + grace`**
(exposed as `threshold_hours` on the row). A weekly job is only genuinely
stuck once a full week plus grace has passed since it was registered.

**Why HTTP and not direct Mongo:** an early version of this check imported
the app's DB connector and pinged the cluster directly from the CI runner.
The runner had no database URI secret, so the ping timed out and the check
was silently marked skipped every morning for days. Plumbing the URI into CI
would have created a new exfiltration surface for a Tier-1 read-only agent;
the `/admin/cron-health` HTTP shim reuses the existing admin-key gate and
adds no new surface. (The read-only Mongo MCP came later, for the
Critical cross-check gate — see
[promotion_criteria.md](promotion_criteria.md) — and this check still prefers
the HTTP shim for its primary read.)

**Recovery (per stuck job — adapt to your host):**

1. Read the app service's logs on the host, filtered to the job id.
2. If the scheduler is missing the job entirely, restart the app worker.
3. If the job is firing but failing, your error tracker already captured the
   exception — fix root cause + redeploy.

**Owner:** whoever owns the scheduled job in question; the app's scheduler
registry is the canonical list.

---

## Maintenance

* When a new bug pattern surfaces twice in production, add it to your
  grep-based code-smell check's pattern allowlist (the production instance
  runs one; the pattern list is the check).
* When a check generates too much noise, lower its severity tier **in this
  file first**, before changing the agent prompt — the rubric is the tunable
  surface, and this catalog is where a tuning decision is visible in review.
* Check ordering changes require updating both this file AND
  `template/.claude/skills/morning-qa/SKILL.md` together — the lockstep
  tests in `tests/` hold them to it.
