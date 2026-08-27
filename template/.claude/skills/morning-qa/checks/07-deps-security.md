# Check 7 — Dependency + security

_Last reviewed: 2026-08-27_

> **Pre-computed:** the raw probe outputs (pip/npm outdated + pip-audit + npm
> audit + the CI-toolchain-pin freshness/advisory facts, Step 6) are normally
> gathered by the pre-step into `/tmp/qa-precompute/bundle.md`
> (see SKILL.md § "Pre-computed inputs").  When this check's block is `OK`, use
> those facts — but the **Accepted-CVE allowlist suppression + each row's
> re-verify greps (Step 4) stay YOURS**; that is a security decision, deliberately
> NOT pre-computed.  The probe steps below are the FALLBACK for a
> `SKIPPED`/`ERROR`/absent block.

## Why this check exists

Dependency advisories arrive on their own schedule, not yours.  This check
gives the operator a daily one-glance answer to "did a HIGH/CRITICAL CVE
land on anything we ship?" — plus passive visibility of the CI tools that
Dependabot structurally cannot watch (Step 6).  The Accepted-CVE allowlist
exists because the alternative — re-triaging the same known-unreachable
advisory every morning — trains the operator to skim past the section
entirely, which is how a NEW advisory gets missed.

## Steps

1. **Backend deps:**
   ```bash
   pip list --outdated --format=json 2>/dev/null | head -200
   pip-audit --strict --format=json 2>&1 || true
   ```
   (If `pip-audit` isn't installed on the runner, skip with a
   Warning — don't fail the run.)

2. **Frontend deps:**
   ```bash
   cd frontend && npm outdated --json 2>/dev/null || true
   cd frontend && npm audit --audit-level=high --json 2>/dev/null || true
   ```
   (Adjust paths to your repo layout; skip a half that doesn't exist.)

3. Filter to:
   * **Outdated** — major version bumps only.  Ignore patch /
     minor jumps.
   * **Audit** — HIGH or CRITICAL severity only.  Ignore moderate/low.

4. **Accepted-CVE allowlist (apply BEFORE severity).**  Cross-reference
   every pip-audit / npm-audit CVE against the **Accepted-CVE allowlist**
   section below.  Suppress a hit (tally under "Accepted (N)", do NOT
   escalate) ONLY when all three hold:
   * the CVE/GHSA id is listed; AND
   * the installed version is still in the CVE's **unfixed range** — judge
     this from the audit tool's own affected/fixed-in fields, NOT a literal
     string match on the table's version column (which only records the
     version seen when the row was written, so an in-range bump like
     1.12.4 → 1.15.0 must STILL match); AND
   * you run the row's **Re-verify each run** check THIS run and it passes.
     Do NOT take the "Why accepted" rationale on faith — run the check
     (e.g. confirm the assumed-unreachable code path really is still
     absent).
   If ANY of the three fails — the CVE is fixed/out-of-range, the re-verify
   check now fails (reachability changed), or the **Remove-when** condition
   is met — the suppression is STALE: treat the CVE as a normal finding per
   the severity table, AND emit a one-line 🟢 Info naming the stale row so
   the operator prunes it.  Erring toward escalation is correct here: an
   over-broad suppression silently hides a real CVE (worse than the noise).

5. Flag any cross-hit between an outdated major and a known CVE.

6. **CI toolchain pin freshness (the Dependabot-blind `npm install -g` tools).**
   The workflow pins two global npm CLIs by exact version —
   `@anthropic-ai/claude-code` and `mongodb-mcp-server` — and installs them
   immediately before the agent step, whose env carries `ANTHROPIC_API_KEY`, the
   Mongo connection string, and the admin keys.  A global install has no
   manifest, so Dependabot cannot watch them (see the framework's
   docs/design.md on toolchain pinning).  The pre-compute bundle carries, per
   tool: pinned version, registry `latest`/`stable` dist-tags, days- and
   stable-releases-behind-latest, and any advisory affecting the **pinned**
   version (from the same bulk endpoint `npm audit` uses — it version-filters
   server-side).  Apply the CI-toolchain rows of the Severity table.
   **Report-only, always:** surface the facts and, on a Warning, name the
   newer version / the advisory — but NEVER recommend or make the bump
   (Tier-1 observer; the operator bumps deliberately after reading release
   notes).

   **Fallback probe** (only when the bundle block is `SKIPPED`/`ERROR`/absent) —
   read the two pinned versions from the `npm install -g` lines in
   `.github/workflows/morning-qa.yml`, then for each `<pkg>`/`<pinned>`:
   ```bash
   # staleness: pinned vs the latest/stable dist-tags (URL-encode the scoped @)
   enc=$(printf '%s' "<pkg>" | sed 's|/|%2F|g')
   curl -sS --max-time 25 "https://registry.npmjs.org/-/package/$enc/dist-tags"
   # CVE against the PINNED version (one call for both tools):
   curl -sS --max-time 25 -X POST -H 'Content-Type: application/json' \
     --data '{"<pkgA>":["<pinnedA>"],"<pkgB>":["<pinnedB>"]}' \
     https://registry.npmjs.org/-/npm/v1/security/advisories/bulk
   ```
   The lightweight dist-tags endpoint gives `latest`/`stable` but not publish
   times, so in the fallback judge staleness from the version gap.  An empty `{}`
   from the advisories endpoint = no CVE; a non-2xx / non-JSON body = **treat as
   unavailable → Warning** (per Edge cases), never a clean bill.

## Accepted-CVE allowlist

CVEs assessed not-reachable in this codebase OR deliberately deferred with
a documented rationale + a concrete removal condition.  A match is
suppressed to the "Accepted (N)" tally, NOT escalated.  Keep it short +
auditable — this is for *documented-accepted* risk, never a
mute-all-low-findings dumping ground.  Every row carries a **Re-verify
each run** check the agent MUST run before suppressing — the rationale is
never taken on faith; that concrete check is the guard against silently
hiding a CVE once its reachability assumption changes.

This template ships the table EMPTY apart from one worked example row,
taken from the production instance with its file paths genericized —
study its shape (especially the re-verify column) before adding your own
rows, then delete it:

| CVE / GHSA | Unfixed range | CVSS | Why accepted | Re-verify each run (must pass to suppress) | Remove-when |
|---|---|---|---|---|---|
| CVE-2024-23342 (GHSA-wj6h-64fc-37mp; pip-audit may report it under a PYSEC alias — match an alias ONLY when the reporting tool's own record lists this CVE/GHSA among its `aliases`; an alias hit WITHOUT that linkage is a different advisory → treat as new, do NOT suppress) | all `ecdsa` versions — no fixed release, upstream WONTFIX | 7.4 (HIGH) | **Not reachable.** Minerva timing side-channel in `ecdsa`'s P-256 sign/key operations.  In the production codebase `ecdsa` was pulled ONLY by `python-jose[cryptography]`, and jose exercises it solely for the ES256/384/512 algorithm family; the sole jose call site decoded RS256-only, and no ES-family algorithm string appeared in any non-test backend file.  Upstream documents side-channel resistance as out of scope for pure Python, so no fixed version will ever ship — there is nothing to bump to.  NB: with the version condition vacuously true forever (all versions affected), the re-verify column is the ONLY live bound on this suppression — keep it strict. | Four file-greps — deliberately no `pip`/env-dependent commands (greps stay valid even when the runner's PATH lacks `pip`).  Tests are excluded on purpose: a negative-path test asserting ES\* tokens get REJECTED must not trip this.  (1) `grep -rn --include='*.py' --exclude-dir=tests 'algorithms=' backend/` → every hit is exactly `["RS256"]`; (2) `grep -rn --include='*.py' --exclude-dir=tests -e ES256 -e ES384 -e ES512 backend/` → zero hits; (3) `grep -rn --include='*.py' --exclude-dir=tests -e 'import ecdsa' -e 'from ecdsa' backend/` → zero hits; (4) `grep -rn --include='*.py' --exclude-dir=tests -e 'from jose' -e 'import jose' backend/` → hits only in the one known auth module.  For (2) and (3), grep exiting 1 with no output IS the zero-hit PASS; any output = FAIL.  If ANY of the four fails → an ES\*/ecdsa path may now be live → do NOT suppress, escalate. | The JWT lib no longer depends on `ecdsa`, OR upstream ships a constant-time fixed release. |

## Severity

Read top-down; first match wins (SKILL.md § "Severity tables read
top-down, first match wins").

| Condition | Severity |
|---|---|
| CVE on the Accepted-CVE allowlist (id + version + reachability still hold) | _suppressed — tally under "Accepted (N)", never escalate_ |
| Any HIGH/CRITICAL CVE matching a pinned **application** dep (backend requirements / frontend bundle; NOT allowlisted) | 🔴 Critical |
| CI toolchain pin (claude-code / mongodb-mcp-server) has a HIGH/CRITICAL advisory affecting the **pinned** version | 🟡 Warning — bump promptly (CI-only tooling → Warning not Critical; see note) |
| CI toolchain pin > 30 days behind the `latest` dist-tag | 🟡 Warning — review + bump |
| CI toolchain pin 14–30 days behind `latest`, OR behind its `stable` tag, OR a moderate/low advisory on the pinned version | 🟢 Info |
| CI toolchain pin == latest (or ≤ 14 days behind) with 0 advisories | _no finding (deliberate pins are expected to trail; quiet avoids alarm fatigue)_ |
| Major bump available for a security-sensitive dep (your crypto, web-framework, auth, and DB-driver packages — enumerate them here) | 🟡 Warning |
| Other majors available | 🟢 Info |
| Clean | _no finding_ |

The CI-toolchain rows are **Warning, not Critical**, even for a HIGH/CRITICAL
advisory: these tools are dev/CI-only, never shipped to a user, and the
Critical bar is data-outage or active user-facing breakage.  The Critical row
above is for **application** dependencies, not these installers.  Staleness keys
on **days**-behind, not raw release count — claude-code cuts many patch releases
a week, so a release count would false-alarm; the pre-compute reports both,
judge on days.  If the advisory check came back **unavailable** (endpoint down /
non-JSON), that is a 🟡 Warning for that tool, not a pass.

## Output format

```markdown
### Check 7 — Deps + security

Backend majors available: {N}
Frontend majors available: {N}
HIGH/CRITICAL CVEs (non-allowlisted): {N}
Accepted (suppressed) CVEs: {N}   {if non-zero, list cve → pin → one-line reason}

CI toolchain pins (Dependabot-blind global installs):
- @anthropic-ai/claude-code: pinned {P} · latest {L} · {behind-summary} · {advisory-summary}
- mongodb-mcp-server: pinned {P} · latest {L} · {behind-summary} · {advisory-summary}

{If non-zero non-allowlisted CVEs, table of name → current → latest → CVE link}
```

Always print the two CI-toolchain-pin lines (facts), even on a clean day — the
operator gets passive visibility of pin drift; a finding only escalates per the
Severity table.

## Edge cases

* `pip-audit` rate-limit / network failure: 🟡 Warning, note it.
  Unavailable ≠ clean — never report a clean bill from a probe that
  didn't run.
* `npm audit` 500: same.
* npm registry / advisories endpoint unreachable or non-JSON (a CI toolchain
  pin's staleness or CVE facts missing): 🟡 Warning, name the tool — an advisory
  check that failed is "unavailable," never a clean bill.
* The agent never recommends a specific bump — it surfaces facts;
  the operator decides.

## Maintaining the accepted-CVE allowlist

Add a row ONLY when a CVE is (a) assessed not-reachable in this codebase,
or (b) deliberately deferred with a documented rationale (in
code/requirements) AND a concrete removal condition.  Every row needs a
**Remove-when** so the suppression self-expires — a permanently-muted CVE
is worse than a noisy one.  Re-audit whenever the underlying assumption
could change (a dependency's transport, exposure, or pin).  An over-broad
entry silently hides a real future CVE, so allowlist changes deserve a
real code review, not a drive-by edit.

Rows may share one re-verify cell — the production instance's three
same-package transport CVEs did (one stdio-only grep pass covered all
three, and a failure escalated all three together).  **When pruning a row
that HOSTS content other rows point at (a shared re-verify, a server
list), relocate that content into a surviving row in the same edit.**
Rows sharing a re-verify can expire at different pin versions, so a
partial bump can prune the host row first; a dangling "run the row
above's check" pointer forces the agent to choose between suppressing
unverified and escalating spuriously.
