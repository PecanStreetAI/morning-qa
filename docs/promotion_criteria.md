# Autonomy promotion criteria

The agent ships at **Tier 1 (Observer)**. Promotion to higher tiers requires
explicit operator approval recorded in this file with a date. This document is
the *only* place tier changes are valid; ad-hoc "let's just turn it on"
decisions don't count.

## Tier 1 — posture, and a worked amendment (2026-06-10)

Tier 1 is read-only, enforced mechanically by the workflow's tool allowlist —
not by prompt. The agent reports findings; it never edits code, never opens
PRs, never closes anything.

The production instance amended Tier 1 once, on 2026-06-10, and the amendment
is preserved here as a worked example of how to reason about a capability
request without silently promoting the agent. Tier 1 gained read-only access
to the application's MongoDB cluster via a read-only Mongo MCP server, to
power a Critical-finding cross-check gate. This did **NOT** promote the agent
to "Tier 1.5" or any higher tier, and the reasoning is the reusable part:

- The MCP server is read-only by configuration (`MDB_MCP_READ_ONLY=true`)
  **AND** the database user it connects as is read-only at the database
  level. Defense in depth: neither layer alone is trusted.
- The workflow pre-approves exactly five read-only verbs (`count`, `find`,
  `aggregate`, `collection-schema`, `list-collections`) — and, because an
  allowlist alone pre-approves rather than restricts, the actual restriction
  is that every write-shaped MCP tool is blocked by `--disallowed-tools` and
  any write-capable server the project runs for other purposes is excluded
  by the dedicated MCP config the QA run loads with `--strict-mcp-config`
  (`template/.mcp.qa.json`), which is separate
  from any interactive-session config.
- A PII rule on the users collection (count + aggregate only; an explicit
  `_id`-or-narrower projection required for `find`) keeps user-identifying
  data out of the GitHub Issue body.
- The capability is **blast-radius-neutral** versus the prior Tier-1 posture:
  the agent could already read every API surface of the running app; it could
  not read the data layer beneath them. Now it can — read-only. That is a
  *fidelity* increase, not a *capability* increase. No new write paths
  exposed.

That last bullet is the test to apply to any future "just give it access to
X" request: does X let the agent *see more truly*, or *do more*? Only the
first is compatible with Tier 1.

**Motivation for the amendment:** a 2026-06-10 Critical false alarm, caused by
the agent pattern-completing JSON under token pressure rather than reading the
actual response. A deterministic database query is the strongest available
signal against that failure mode; granting Tier 1 the read-only MCP made the
cross-check gate executable in CI. (The shipped `LESSONS.md` starts empty —
the incident is narrated where the gate is specified, in SKILL.md § "Mongo
cross-check gate for Critical findings".)

Promotion to Tier 2 still requires the gates below. At extraction time, none
had been met by the production instance.

## Tier 1 → Tier 2 (Observer → Proposer)

**Tier 2** lets the agent open PRs. The operator still merges; the agent never
auto-merges anything at Tier 2.

**Gates (all required, no exceptions):**

1. **≥ 14 consecutive days of Tier 1 runs** with no operator-confirmed
   false-positive Critical findings.
2. **≥ 3 operator-confirmed true-positive findings** of any severity. If the
   agent hasn't surfaced 3 real issues in 14 days, it isn't earning its
   keep — fix the checks before expanding scope.
3. **An explicit allowlist** of which check categories advance. Not a blanket
   promotion. Sample allowlist (illustrative — fill in when actually
   promoting):
   * A housekeeping check → mark items done when their resolution receipts
     are already in the repo.
   * A code-smell check → open PRs that remove unambiguous dead imports
     flagged by the linter.
   * **Everything else stays at Tier 1.**
4. **Operator signature** below — name, date, link to the proposal Issue or
   commit.

### Tier-2 shadow mode (the on-ramp — active at Tier 1)

Before promotion the agent runs in **shadow mode**: for a small set of
low-blast checks the operator designates, it DRAFTS the would-be fix as a diff
inside its daily Issue, labeled "DRAFT — not applied", with zero write
capability. This is fully Tier-1-compliant (no Edit / Write / PR) and exists
so the operator can judge the *quality* of the agent's proposed fixes before
granting it the ability to actually open them. Mechanism and scope:
`template/.claude/skills/morning-qa/SKILL.md` § "Tier-2 shadow mode".

**Gate tracking lives in [calibration_ledger.md](calibration_ledger.md):**
- Gate #1 (≥ 14 clean days) — the clean-day streak, and any Critical-FP
  resets.
- Gate #2 (≥ 3 confirmed true positives) — the TP tally.

A shadow draft the operator confirms was correct and worth landing counts as a
true positive toward gate #2. The earliest possible promotion date is always
14 days from the current clock start in the ledger — and only if both gates
are met by then.

### Tier 2 promotion log

*(empty — none yet)*

## Tier 2 → Tier 3 (Proposer → Fixer)

**Tier 3** lets the agent auto-merge inside a narrow enumerated allowlist.
Requires the strictest gates.

**Gates (all required):**

1. **≥ 30 days at Tier 2** with **zero operator-rejected PRs** in the
   allowlist categories.
2. **CI must be the only auto-merge gate** — at minimum: the full test suite,
   any registry/consistency audits your project runs, lint with
   `--max-warnings 0`, and a build of everything that builds.
3. **Auto-merge only for PRs that touch ONLY** files in the enumerated
   allowlist. PRs that touch any file outside the allowlist drop back to
   Tier 2 (human-merged).
4. **Per-category trial period** — each newly-promoted category runs in
   Tier-2 shadow for 7 days first (the agent opens the PR labeled
   `tier-3-candidate`; if the operator approves all 7 days running, promote).
5. **Kill switch** — a single env var (`QA_AGENT_TIER3=false`) reverts every
   Tier-3 path back to Tier 2 instantly. Tested monthly.
6. **Operator signature + the explicit allowlist** below.

### Tier 3 promotion log

*(empty — none yet)*

## Demotion

Any tier can be demoted immediately, no committee needed.

**Demotion triggers (any one):**

* An operator-confirmed false positive that caused user-visible downtime or
  required a revert.
* A merged PR (Tier 2/3) that broke a live environment.
* 3+ consecutive days of Critical findings the operator marks as noise.
* Operator gut-feel — write it down here when you act on it.

Demotion procedure: edit the workflow's tool allowlist + add a line under
"Demotion log" below. The agent picks up the change on its next scheduled run.

### Demotion log

*(empty — none yet)*

## What promotion does NOT change

* **Tier 1 checks always run.** Even at Tier 3, the observer checks fire
  daily and post the Issue. The Fixer behaviors layer on top.
* **Critical findings are always operator-visible.** The agent never silently
  resolves a Critical, regardless of tier.
* **This file is the source of truth.** If the workflow ever drifts from what
  is documented here, the workflow is wrong and must be reverted to match.
