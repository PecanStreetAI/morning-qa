# morning-qa

A **graduated-autonomy, read-only QA observer** for your codebase and your
live services: a scheduled Claude Code run that executes a roster of
read-only checks every morning and posts **exactly one GitHub issue per
day** with what it found — or an explicit "all clean."

It is the opposite of an autonomous fixer. The agent starts with no ability
to change anything, has to earn every increase in autonomy through recorded
gates, and is measured by a ledger it is structurally barred from writing.

## The core loop

Every morning, a GitHub Actions cron:

1. **Precomputes** the deterministic inputs — HTTP probes, package audits,
   git reads — into one bundle, before any model is in the loop
   ([docs/precompute.md](docs/precompute.md)).
2. **Runs Claude Code** with the `morning-qa` skill and a read-only tool
   allowlist. The agent reads the bundle, applies each check's severity
   rubric, and writes a Markdown report.
3. **Posts one issue**, titled `[qa-agent] {YYYY-MM-DD} morning report`,
   labeled `qa-agent-daily`. Yesterday's issue is closed by today's run
   unless it was pinned Critical.

Clean days produce an issue that says so. Silence is ambiguous between "no
findings" and "the agent didn't run," so the loop never uses silence to mean
anything.

| Severity | What the agent does | What you do |
|---|---|---|
| 🔴 Critical | Pins the issue + adds `priority:critical` | Triage same-day |
| 🟡 Warning | Standard issue | Read during normal review; decide if it warrants a fix this week |
| 🟢 Info | Standard issue | Skim; the common case is "noted, nothing to do" |
| ⏳ Incomplete | Adds `qa-agent-incomplete` — the run ended early, so today's coverage is **unknown** | Read the partial report, decide whether to re-dispatch. Not a clean day, and not a finding |

The agent errs toward Warning when uncertain. Critical should be rare (≤1×
per week at steady state). If you find yourself ignoring Criticals because
they're noisy, that's the signal to retune thresholds — not to stop reading.

## The autonomy ladder

Three tiers, from [docs/design.md](docs/design.md):

* **Tier 1 — Observer** (where the agent ships, and where the production
  instance still runs): reports findings only. Never edits code, never opens
  PRs, never closes anything.
* **Tier 2 — Proposer:** opens PRs for enumerated check categories; a human
  merges every one.
* **Tier 3 — Fixer:** auto-merges inside a narrow file allowlist, with CI as
  the only gate and a single-env-var kill switch (`QA_AGENT_TIER3=false`)
  that reverts everything to Tier 2 instantly.

The tier is enforced **mechanically**, not by prompt — and by the right
mechanism: the CLI's `--allowed-tools` is a *pre-approval list, not a
restriction* (a lesson the production instance learned when an off-list tool
ran anyway), so what actually makes Tier 1 read-only is the
`--disallowed-tools` blocklist plus `--strict-mcp-config` (only the dedicated
read-only MCP config loads), with the skill's hard constraints layered on
top. A tier change is a change to those flags, recorded in
[docs/promotion_criteria.md](docs/promotion_criteria.md) — the only place a
tier change is valid. Promotion runs through quantitative
gates (≥14 consecutive days with no false-positive Critical, ≥3
operator-confirmed true positives), demotion is immediate and needs no
committee, and before any promotion the agent runs **shadow mode**: it drafts
its would-be fixes as diffs inside the daily issue, labeled "DRAFT — not
applied," so the operator can judge fix quality while the agent still has
zero write capability.

The gates are tracked in
[docs/calibration_ledger.md](docs/calibration_ledger.md) — **owned by the
operator, never the agent**. A weekly retro workflow reads the week's issues
and the operator's TP/FP verdict comments and *proposes* ledger rows in a
sign-off issue; it never writes the ledger, and the agent never
self-certifies a finding. A Critical false positive resets the clean-day
clock. An open, un-verdicted Critical is not a clean day — it is an
unmeasured one.

## Severity and completeness are separate axes

A run that dies mid-flight (API drop, the 30-minute cap) posts whatever
partial report was on disk, labeled `qa-agent-incomplete`.
`priority:critical` answers "is there a finding to triage"; the incomplete
label answers "did we establish coverage at all." Both can apply at once.

This distinction was earned, not designed: until 2026-08 the in-progress
report skeleton carried a "max severity: none" marker, so a run that
completed **zero** checks posted the same benign, unlabeled issue as a run
that completed all of them and found nothing. The fix gave the in-progress
state its own marker value and its own label — and the general form of the
lesson ("unavailable ≠ clean," at every layer it recurs) is written up in
[docs/precompute.md](docs/precompute.md).

One failure mode deliberately gets a louder label instead: if the agent job
dies so hard it uploads no report artifact at all (runner offline, job
cancelled), the posting job **synthesizes a Critical** — "🔴 run job produced
no artifact" — so that day pins open rather than filing quietly under
incomplete. A vanished job is a harder failure than a truncated one. When
auditing observer health, search both labels.

## Self-feedback and dwell escalation

The precompute bundle carries yesterday's issue (and the operator's comments
on it), so today's run reads yesterday's report before writing its own.
That enables **dwell escalation**: rubrics can say "this 🟡 on two
consecutive mornings → treat as Critical," with yesterday's value riding the
verbatim issue body — no extra queries. The motivating incident: a metrics
collector that failed 100% of its polls for six days while every heartbeat
stayed green (see Check 9 in [docs/check_catalog.md](docs/check_catalog.md)).
A one-day Warning is a datum; the same Warning dwelling is an outage.

The "yesterday" fetch deliberately skips issues created today — otherwise a
same-day re-run reads its own report as yesterday's and every self-feedback
check finds "no change" by construction. That, too, was a shipped bug first.

## The Mongo cross-check gate for Criticals

Before the agent may post a Critical, it must verify the underlying fact with
a deterministic read-only database query — via a dedicated read-only MCP
server, allowlisted to five read verbs, doubly read-only (server config AND
database user). This gate exists because of a measured failure mode: a
Critical false alarm caused by the agent pattern-completing JSON under token
pressure instead of reading the actual response. A deterministic query is
the strongest available signal against that.

When the database credential is absent, the gate degrades honestly:
Criticals post with an **`[UNVERIFIED]` suffix** plus a meta-finding saying
the gate was unavailable — never silently dropped, and never silently
"verified." The invariant underneath the whole system: **unavailable ≠
clean.** A check that cannot see must say it is blind; it must never say ✅.

## Cost engineering, with receipts

The numbers are from the production instance and are part of the design, not
an afterthought ([docs/design.md](docs/design.md) § Cost):

* **The model is pinned** via `--model` in the workflow — because the unpinned
  CLI default drifted to an Opus-class model and silently billed ~$6.40/day
  (~$190/month, measured from run artifacts) before anyone noticed; roughly
  30x the original estimate. A default is a price that changes with CLI
  upgrades. At the pin: ~$2.60–3.80/run.
* **Every daily issue ends with a `Run telemetry:` footer** — model, cost,
  turns, minutes — so drift is visible the morning it happens. The footer is
  best-effort observability, not an integrity boundary; your API console
  remains the source of truth.
* **Turn count, not model choice, drives cost and timeout risk** (a 2026-07
  finding: the model pin lowered per-turn cost while total cost rose). The
  precompute bundle is the response — ~50 deterministic probes moved out of
  the model loop — and the 30-minute cap was deliberately kept so the saving
  has to be earned, not masked.
* **Every daily-installed input is version-pinned** — the Claude CLI and MCP
  server by exact npm version, actions by commit SHA — because these installs
  run unattended, daily, immediately before the step whose environment holds
  the secrets. Resolved at `latest`, a compromised release executes there
  within 24 hours with no repo change to review. Check 7 watches the pins
  daily so they are reviewably stale, never silently stale.

## Docs and code are kept in lockstep by tests

Severity tables, allowlist entries, and the precomputed-check set each live
in more than one place (a check spec, this catalog, the script). The tests in
`tests/` pin the mirrors to each other, so a one-sided edit fails CI at PR
time instead of surfacing as doc drift weeks later. The documentation is part
of the machine, and the tests hold it to that.

## What ships here

Three worked-example checks, chosen because they generalize to almost any
project:

* **Check 0 — disk vitals**: a pre-flight that stops a full runner disk from
  crash-looping every downstream check.
* **Check 7 — dependency + security**: audits, major-version drift, an
  accepted-CVE allowlist, and the CI-toolchain pin watch.
* **Check 9 — cron heartbeats**: the stuck-job, split-brain, and
  zero-yield ("green heartbeat, dead work") detectors for scheduled jobs.

Plus the check-spec **TEMPLATE** for writing your own. Full rubrics:
[docs/check_catalog.md](docs/check_catalog.md). The production instance this
was extracted from has grown the same skeleton to **17 checks run daily since
2026-05** — data freshness, error-tracker triage, value-integrity recomputes,
SEO health, and more. The framework is the part that transfers; the roster is
yours to grow.

And one **case study**
([docs/case_studies/](docs/case_studies/2026-08-11-dwell-escalation-first-fire.md)):
a complete detect → diagnose → fix loop from the production instance — the
dwell-escalation rule's first-ever fire, the forensic diagnosis under it, and
a dated Outcome section added five days later that scores the original claims
honestly, including the one that only partially held. A detector is only
credible with worked examples of what it caught and where its own calibration
was validated or challenged; a case study that never returns to check its own
claims is marketing, not evidence.

## Repo map

```
template/                     ← what YOU copy into your repo root
  .claude/skills/morning-qa/  ← the skill: SKILL.md, checks/ (00, 07, 09,
                                TEMPLATE.md), LESSONS.md
  .github/workflows/          ← morning-qa.yml, qa-ledger-retro.yml
  .github/scripts/            ← qa_precompute.py, qa_severity_label.sh,
                                qa_run_telemetry.js, qa_ledger_retro.py
  .mcp.qa.json                ← the QA run's own MCP config (read-only Mongo)
docs/                         ← the design record: design, check catalog,
                                precompute, promotion criteria, ledger,
                                case_studies/
tests/                        ← the lockstep pins (spec ↔ catalog ↔ script)
scripts/denylist_scan.sh      ← the sanitization backstop (see PORTING.md)
```

`template/` is a template, not an installation: the workflows live under
`template/.github/`, which is **why nothing executes in this repo** — GitHub
only runs workflows found at a repo's own `.github/workflows/`. They run in
*your* repo, after you copy them there.

## Getting started

1. **Copy `template/*` into your repo root** (merging `.claude/` and
   `.github/` with whatever you have).
2. **Set the secrets the workflow names** — at minimum `ANTHROPIC_API_KEY`;
   `ADMIN_API_KEY` if you wire up Check 9's cron-health endpoint;
   `MDB_MCP_CONNECTION_STRING` (read-only!) if you want the Critical
   cross-check gate — without it, Criticals post as `[UNVERIFIED]`.
   Also set the repo **variable** `APP_BASE_URL` to your deployed app's
   origin — Check 9's probe and the deploy-state receipt read it, and both
   self-skip while it's unset. `morning-qa.yml` is the authority on the full
   list; every optional secret self-skips gracefully when unset.
3. **Adjust the three checks** to your stack — endpoints, job registry,
   allowlists — and keep the catalog + spec mirrors in sync (the tests show
   you how). Also adjust the dependency-install paths in `morning-qa.yml`
   (`backend/requirements.txt`, `frontend/` — worked examples for Check 7;
   point them at your manifests, or delete the halves you don't have, or
   run-qa dies before the agent ever starts and every day posts the
   synthesized 🔴 "no artifact" issue).
4. **Create your calibration ledger** at `docs/calibration_ledger.md` in
   *your* repo (copy this repo's as the format) or set `QA_LEDGER_PATH` in
   `qa-ledger-retro.yml` — the weekly retro's dedupe deliberately fail-opens
   when the file is missing, so without one it silently re-proposes rows
   forever.
5. **Read [docs/promotion_criteria.md](docs/promotion_criteria.md) before
   granting anything beyond Tier 1.** The ladder is the product; skipping it
   gets you an autonomous fixer with extra steps.

Killing it is as clean as starting it: disable the workflow (next run stops),
cancel any in-flight run, or delete the workflow file and revoke the API key.
No agent state persists outside GitHub Issues and the run logs.

## Provenance

Extracted at baseline 2026-08-27 from the production QA system of a
real-estate market-analytics platform, where it has run daily since
2026-05-19. Improvements flow one way, private → public, through a
sanitization checklist — see [PORTING.md](PORTING.md). MIT licensed
([LICENSE](LICENSE)).
