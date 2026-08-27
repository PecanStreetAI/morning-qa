# Design

**Status:** extracted 2026-08-27 from a production instance that has run at
Tier 1 (Observer) since 2026-05-19. This document preserves the design as
proposed and then hardened in production; tier promotions amend
[promotion_criteria.md](promotion_criteria.md) rather than this file. The
check roster and severity rubrics live in [check_catalog.md](check_catalog.md).

## Goal

A scheduled Claude Code run that audits your codebase and your live services
each morning and surfaces issues to the operator before they hit users. It is
deliberately **not** a fully-autonomous fixer — see the next section for why.

## Why graduated autonomy

A truly autonomous "find and fix any known or unknown issues" agent fails in
three predictable ways:

1. **Drift commits** — the agent rewrites code it doesn't fully understand;
   the diff sits in main until someone notices a regression weeks later.
2. **Noise floods** — the agent flags 40 findings per day; the operator starts
   ignoring the channel; the one real issue per week hides in the stream.
3. **Trust rot** — once one autonomous fix breaks a live environment, the
   operator spends more time un-trusting the agent than the agent ever saved.

The mitigation is a **graduated autonomy ladder** with explicit promotion
gates:

| Tier | Action | Examples | Auto-merge? |
|---|---|---|---|
| **1. Observer** | Reports findings only | Error-tracker triage, ingest audits, drift detection | Never |
| **2. Proposer** | Opens PRs for human review | Stale comments, dead code, typos, small test additions | Operator merges |
| **3. Fixer** | Auto-merges in a narrow allowlist | Housekeeping edits, doc-title fixes, low-risk dep bumps that pass CI | Inside enumerated paths only |

The agent ships — and the production instance still runs — at Tier 1 only.
Tier 2/3 promotion rules live in
[promotion_criteria.md](promotion_criteria.md). The ladder is enforced
**mechanically**, not by prompt: the workflow's `--disallowed-tools`
blocklist + `--strict-mcp-config` are what make Tier 1 read-only
(`--allowed-tools` only pre-approves — it does not restrict; see the
workflow's own comments), and a tier change is a change to those flags.

## Timing

The production instance runs at **10:00 UTC daily**, chosen to land a few
hours after its daily batch/ingest jobs are expected to complete — buffer for
slow days, but still inside the operator's morning window. When you adopt the
template, pick a time with the same two properties: downstream of the jobs
your checks will audit, and upstream of the human who reads the report. It is
worth writing down (as the original proposal did) which other crons run near
your chosen slot, even the ones that don't conflict — the ecosystem note pays
for itself the first time two schedules drift toward each other.

## What it runs

Checks execute in a fixed order, sorted by signal-to-noise, so that a hard
fail on an early check (for example, "the database is unreachable") can
short-circuit the run instead of burning tokens on downstream checks that are
guaranteed to fail for the same reason.

This repo ships three worked-example checks — disk vitals (Check 0),
dependency + security (Check 7), and cron heartbeats (Check 9) — plus the
spec template for writing your own
(`template/.claude/skills/morning-qa/checks/TEMPLATE.md`). The production
instance this was extracted from has grown the same skeleton to 17 checks
run daily since 2026-05 (16 numbered checks plus the disk-vitals
pre-flight — docs elsewhere that say "the 16-check roster" are counting
the numbered checks only). The full per-check format and the three shipped
rubrics are in [check_catalog.md](check_catalog.md).

## Platform choice

GitHub Actions + the Claude Code CLI. Considered and rejected:

* **Anthropic scheduled routines (cloud-hosted):** runs in Anthropic's cloud
  (good — survives if your CI is broken) but doesn't have repo write access by
  default and is harder to feed the latest repo state to. Worth re-evaluating
  if a Tier 2 promotion ever argues for independence from your CI.
* **A standalone daemon on a small VM:** maximum control, but yet another
  thing to operate. Skipped.

GitHub Actions wins because:

* It reuses infrastructure you already have (an `ANTHROPIC_API_KEY` secret,
  Actions billing, Issues for output, Actions logs for full transcripts).
* The CLI's tool flags enforce tier discipline **mechanically** — Tier 1 is
  a flag set (`--disallowed-tools` + `--strict-mcp-config`; the allowlist
  merely pre-approves), not a promise.
* Promotion to Tier 2 later is an incremental change — add `Edit` and an
  auto-PR workflow step — not a re-platform.
* Cost is bounded and observable: the model is pinned in the workflow, and
  per-run cost is surfaced as a footer on each daily issue (see § Cost).

## Toolchain pinning

Every input this job installs is version-pinned: `uses:` actions by commit
SHA, Python audit tooling by exact version, and — since 2026-07-23 in the
production instance — `@anthropic-ai/claude-code` and `mongodb-mcp-server` by
exact npm version instead of a bare name or `@latest`.

Why it matters more here than on a normal CI job: these installs run
**unattended, daily, immediately before** the step whose environment carries
`ANTHROPIC_API_KEY`, `MDB_MCP_CONNECTION_STRING` (a read-only URI which, in a
real deployment, points at a cluster holding live user data),
`API_ACCESS_KEY`, `ADMIN_API_KEY`, and the GitHub token. Resolved at
`latest`, a compromised or account-takeover release executes in that
environment within 24 hours with no repo change to review. It is the same
silent-upgrade class as the unpinned-model drift in § Cost — that one cost
money; this one costs secrets.

Secondary benefit: a pinned CLI also freezes the `stream-json` wire shape that
`template/.github/scripts/qa_run_telemetry.js` parses, so the cost footer
can't degrade overnight from a producer-side change.

**To bump** (deliberate, never automatic): edit the version in
`template/.github/workflows/morning-qa.yml`, read the release notes, ship it
as its own PR, then confirm on the next daily run that skill loading,
`--model`, and the telemetry footer all still work. A pin test in `tests/`
fails CI if either install line reverts to a bare name, a dist-tag, or a
semver range. Keep a BUMP LOG comment on the install step recording each
bump's trigger, release-note read, and expected deltas (the production
instance's first bump, 2026-08-23, took the CLI 2.1.218 → 2.1.241, triggered
by Check 7's own >30-days-behind Warning). A bump is not clean until the
first post-merge daily run's footer and report have been read.

Residuals, accepted (surfaced by a review pass on the pinning change itself):

* `mongodb-mcp-server` ships no shrinkwrap and its own dependencies carry
  caret ranges, so its transitives still re-resolve daily.
  `@anthropic-ai/claude-code` publishes no floating runtime deps, so its pin
  freezes the whole tree. Record the exact dependency shape and the re-verify
  command in a comment next to the pin.
* Dependabot cannot see a global `npm install -g` (no manifest), so nothing
  automatic refreshes these two pins. The production instance closed that
  residual by making **Check 7 watch them daily**: the precompute step reads
  each pinned version straight from the workflow's own install lines — so the
  watcher can never drift from the pin it checks — and, against the public npm
  registry, gathers the `latest`/`stable` dist-tags, how many days and stable
  releases the pin is behind, and any advisory affecting the **pinned**
  version (the same bulk endpoint `npm audit` uses, version-filtered
  server-side). The agent reports 🟢 Info as the pin drifts, 🟡 Warning once
  it is >30 days behind or an advisory lands. Report-only — bumping stays the
  deliberate human step. The accepted trade is therefore "**reviewably**
  stale — and surfaced the morning a newer release or a CVE appears" rather
  than "silently stale." Rubric and fallback probe:
  `template/.claude/skills/morning-qa/checks/07-deps-security.md` § Step 6;
  extractors pinned by the tests in `tests/`.

### Materializing secrets into workflow files (two field lessons)

The starter roster needs no secret written to disk, but the moment a
check does (a service-account JSON, a config file the agent reads),
two gotchas from the production instance apply:

* **Store multiline/JSON secrets base64-wrapped, decode at
  materialize time.** GitHub Actions registers every *line* of a
  secret as a log-mask token; a raw JSON secret makes the leading `{`
  (and `}`, and every short line) a mask token, garbling unrelated log
  lines to `***` across the whole job. `base64 -d > file` at the
  materialize step sidesteps the masking entirely and survives quoting
  intact.
* **Write with `printf '%s'`, never `echo`, and verify by byte count
  only.** `echo` mangles backslash escapes on some shells, and any
  verification that prints content risks the leak the secret-handling
  rules exist to prevent — `wc -c` against the expected length is the
  whole confirmation.

## Output contract

One GitHub Issue per day, titled `[qa-agent] {YYYY-MM-DD} morning report`,
labeled `qa-agent-daily`. Empty days produce an Issue saying "✅ all checks
clean" — silence would be ambiguous between "no findings" and "the agent
didn't run."

Critical findings additionally pin the Issue and apply `priority:critical`.
The threshold for Critical: data-outage potential or active user-facing
breakage. Everything else is Warning or Info.

**Severity and completeness are separate axes** (hardened 2026-08-06 after a
production incident — see [precompute.md](precompute.md) § "Unavailable ≠
clean" for the full story). A run that dies mid-flight posts whatever partial
report was on disk, and that report is labeled `qa-agent-incomplete` — driven
by an explicit `unknown` marker value, or by a Status line still reading "⏳
In progress" at post time. `priority:critical` answers "is there a finding to
triage"; the incomplete label answers "did we establish coverage at all."
Both can apply at once. The classification logic lives in
`template/.github/scripts/qa_severity_label.sh` — extracted to a standalone
script after its third inline-in-YAML bug — and is pinned by the tests in
`tests/`.

## Cost

(Rewritten 2026-07-23 in the production instance with measured numbers — the
original 2026-05 estimates this section replaced were off by ~30x by July.
The numbers below are that instance's; treat them as an honest reference
point, not a promise.)

* The model is **pinned via `--model` in the workflow** (`claude-sonnet-5` at
  extraction time). **Never run this workflow on an unpinned default.** The
  original design said "Sonnet, model default"; the CLI default then drifted
  to an Opus-class model (`claude-opus-4-8[1m]`) and the run silently billed
  $5.88–$6.84/day (~$190/mo, measured from the run artifacts' stream-json
  `result` events) before anyone noticed. A default is a price that changes
  with CLI upgrades.
* The original token estimate (~30K in / ~5K out) was long obsolete by the
  time anyone measured: the grown 16-check production run measures ~6.5M
  cache-read + ~0.28M cache-write + ~12K fresh-input + ~53K output tokens
  across 69–81 turns (~15–23 minutes).
* Expected at the Sonnet pin: **~$2.60–3.80 per run/day** ≈ **$80–115/month**
  at 2026 pricing.
* Each daily issue ends with a `Run telemetry:` footer (model, cost, turns,
  minutes — parsed from the `result` event's `total_cost_usd` by
  `template/.github/scripts/qa_run_telemetry.js`), so drift is visible the
  morning it happens.
* The footer is **best-effort observability, not an integrity boundary**: it
  parses a log writable by the same OS user the agent's Bash tool runs as, so
  it reliably catches *accidental* drift (the failure mode that actually
  occurred) but a hijacked agent could in principle forge it. Your Anthropic
  Console / invoice remains the source of truth for cost auditing — a clean
  footer is never proof of a clean spend.

If a run's footer shows ~2x the expected per-run cost, or a model other than
the pinned one, something is wrong (retry loops on a failing check, an
unreviewed pin change, a CLI behavior shift) — pause the workflow and
investigate.

**Turn count is the real cost + timeout lever** (2026-07-27 finding).
Post-pin telemetry showed the `--model` pin lowered *per-turn* cost but total
run cost and time rose, because turns rose to 108–125 — cost is dominated by
turn count, not model choice. The **deterministic precompute step** is the
response: it runs the mechanical probes once, before the agent, so the agent
reads one bundle instead of making ~50 data-gathering round trips. Fully
fail-soft; the 30-minute job cap was deliberately **kept** so the saving has
to be earned, not masked. Design and contract: [precompute.md](precompute.md).

## Failure modes anticipated

| Failure | What happens | Mitigation |
|---|---|---|
| Anthropic API outage | Workflow fails; no Issue posted | GitHub Actions failure email; the operator notices the absence within a day |
| `ANTHROPIC_API_KEY` rotated, secret stale | Workflow fails on first API call | Standard secret-rotation runbook |
| The monitored app or its error tracker is down at run time | The relevant check reports the outage | Agent classifies as Critical; operator acks |
| Workflow run exceeds 30 min | GitHub kills it; the partial report posts with `qa-agent-incomplete` | The precompute bundle cuts turns; if still tight, trim checks/context before raising the cap |
| Agent hallucinates a false-positive Critical | Operator records the FP verdict (it resets the promotion clock — see [calibration_ledger.md](calibration_ledger.md)) | Tune the relevant check; document the failure mode |

## Future enhancements (parked)

* **Trend visualization** — instead of one Issue per day, emit JSONL to a
  small dashboard. Useful once you have ≥30 days of data.
* **Chat mirror** — pipe Critical findings to a Slack/Teams channel for
  faster surfacing. Cheap to add when there's a workspace to pipe into.
* **Cross-day correlation** — the agent reads the prior 7 days' Issues before
  generating today's and surfaces patterns ("this is the 3rd time this week
  the ingest was slow"). Requires Tier 1 to have run cleanly for ≥7 days
  before it's worth building. (The production instance ships a narrow version
  of this: the precompute bundle's YESTERDAY block plus per-check dwell
  escalation rules — a Warning that persists N consecutive mornings is
  treated as Critical.)
