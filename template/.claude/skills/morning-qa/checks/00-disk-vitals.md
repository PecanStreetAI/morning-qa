# Check 0 — Disk vitals (pre-flight)

_Last reviewed: 2026-08-27_

> **Pre-computed:** raw data for this check is normally gathered by the pre-step
> into `/tmp/qa-precompute/bundle.md` (see SKILL.md § "Pre-computed inputs").
> When this check's bundle block is `OK`, use those facts and apply the severity
> rubric directly — the probe steps below are the FALLBACK for when the block is
> `SKIPPED`/`ERROR`/absent.

## Why this check exists

Runs FIRST, before every other check.  A self-hosted runner has a fixed
root volume, and a full disk crash-loops the runner agent and every
downstream check.  The production instance learned this the hard way in
a 2026-06-12 incident: the runner's root volume hit 100% and the QA run
failed five mornings in a row wearing three different masks — npm
`ENOSPC` errors, the runner agent crash-looping because it could not
write its own diagnostic log, and GitHub simply showing the runner
"offline."  None of the three said "disk full."  This check makes the
real cause visible BEFORE it bites anything downstream.

(If your workflow runs on GitHub-hosted runners, disk pressure is far
less likely but the check still costs one Bash call — keep it as a
cheap canary.)

## Steps

1. Read root + tmp filesystem usage — `df` + `awk` only, no external tools:
   ```bash
   df -P -k / /tmp 2>/dev/null \
     | awk 'NR>1 {printf "%s  used=%s  avail_gb=%.1f\n", $6, $5, $4/1048576}'
   ```
   In `df -P -k` output, `$5` is the capacity percentage (e.g. `73%`) and
   `$4` is available KiB.  The root (`/`) used-% is the load-bearing number.
2. IF root is ≥ Warning, surface the biggest cache/workspace consumers so
   the operator gets a one-glance prune target.  Best-effort — never fail
   the check on this:
   ```bash
   du -sh ~/.npm/_cacache ~/.cache/pip "${RUNNER_WORKSPACE:-/tmp}" 2>/dev/null \
     | sort -rh | head -3 || true
   ```

## Severity

| Condition (root `/` used %) | Severity |
|---|---|
| ≥ 95% | 🔴 Critical — CI failure imminent; the runner will crash-loop |
| 80–94% | 🟡 Warning — trending unsafe; prune caches / grow the volume soon |
| < 80% | 🟢 Info — healthy; report the current % for trend tracking |

A Critical here **SHOULD short-circuit the run** (per SKILL.md "Run the
checks" short-circuit rule): at ≥95% the downstream checks
(`npm ci` / `pip install` / `pytest` / the agent workspace) will
themselves fail or mislead.  Emit the Critical, note the short-circuit in
the report, and stop.

This check is **not** Mongo-gated — the cross-check gate exists to verify
data-layer Criticals against the weak curl+JSON-interpretation link; a disk
Critical is an infra fact `df` reports directly, with no interpretation
layer to second-guess.

## Output format

```markdown
### Check 0 — Disk vitals (pre-flight)

Root `/`: {used}% used, {avail} GB free.  `/tmp`: {used}%.
{If ≥ Warning: top cache/workspace consumers + suggested prune.}
{Trend vs yesterday's Info line, if the prior report cached it.}
```

## Notes

- **Local vs CI.** On a manual invocation from the operator's laptop,
  `df /` reports the laptop disk, not the runner — still a valid read, but
  frame it as "local invocation" so a healthy laptop isn't mistaken for a
  healthy runner.  The scheduled CI run on the runner is the
  authoritative source.
- `RUNNER_WORKSPACE` is a GitHub-Actions env var present only in CI; the
  `du` line degrades gracefully (`${RUNNER_WORKSPACE:-/tmp}` + `|| true`)
  when run locally.
- Hardware facts (instance sizing, volume size, any live-grow procedure)
  belong in your runner's operator runbook, not here — this check reads
  the symptom, the runbook owns the remedy.
