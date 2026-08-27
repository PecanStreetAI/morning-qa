# Case study — the dwell-escalation's first fire (2026-08-11) and its follow-up drive

_This is a sanitized worked example from the production instance this
framework was extracted from. Company, market, vendor, and system names
have been genericized (City A / City B / …, "the content pipeline",
"the vendor"); dates, severities, mechanisms, numbers, and verdicts are
preserved as recorded. The checks exercised here come from the
production instance's fuller roster (16 numbered checks plus a
pre-flight — the "17 checks" of the README) — the public repo ships 3
example checks, but the detect → diagnose → fix pattern is identical._

_**Convention this entry establishes:** the body is written at fix
time, and a dated **Outcome** section is appended later, once
subsequent independent runs have scored the predictions. A case study
that never returns to check its own claims is not evidence. See
"Outcome — five days later" below, which confirms three claims, adds a
calibration finding on the value-integrity check, downgrades one claim
to partially-confirmed, and records a Tier-2 note that predicted a real
recurrence._

## The run

The daily report of 2026-08-11 (10:20Z): **1 Critical, 3 Warning,
6 Info** across the 16-check roster. The Critical was the **first-ever
firing of a check's dwell-based auto-escalation rule** (the
content-freshness check: "Warning persisting ≥ 7 consecutive days →
treat as Critical") since that rule was written. Same evening, the
operator directed a follow-up session to drive every finding to
completion with zero intervention. Everything below was produced by
that session (Tier-1 boundaries respected — the *observer* never
edits; the follow-up session is an ordinary operator-directed session
with normal autonomy).

## Finding-by-finding

### 🔴 Critical #1 — per-market content-pipeline starvation (content-freshness check, day 7)

**Detected:** City A / City B / City C held gate-passing June-2026
cards for 7 consecutive days while 17 peer markets had published that
month; 3 / 9 / 9 gate-passing drafts skipped; the monthly publication
window permanently closed. All four of the check's independent guards
re-verified against the live database each day.

**Diagnosis (follow-up):** all three markets share a
`status:"skipped"` publication record stamped 2026-07-21 — a **bulk
data-correction's mass-flip** (183 freshly-generated drafts flipped to
`skipped` by a raw `update_many`; identical `updated_at` millisecond
across all rows; 100% of every skipped row ever written). Content ids
in the pipeline are month-anchored, so when the data correction
recomputed the June cards under the *same* ids, the anti-recycle guard
("has this story already been published?") honored the tombstones
forever. The three starved markets are exactly those whose **only**
top-strength June card had headlined that batch. Notably, the three
tombstoned stories' stored content matches today's corrected cards —
they were collateral of an indiscriminate bulk verb, not bad stories.

**Outcome:** the fix PR (merged 2026-08-12) — skips now carry an
attribution stamp recording *what* skipped them; only attributed skips
tombstone; a new recovery endpoint clears a tombstone (with a
duplicate-publish guard); the operator runbook gains a discard-vs-skip
decision table and a recovery recipe for the three lost June stories
(an operator decision — their rendered snapshots are intact and
honestly cited).

**Detector verdict:** the guard stack named exactly the right markets,
the day-7 escalation fired on schedule, and the suspected cause
("pipeline defect, not lag") was correct. End-to-end precision
validated on the rule's first real firing.

### 🟡 Warning #2 — global content freshness / vendor July data (content-freshness check, day 2)

**Detected:** newest publication 18 days old, frontier lag 2 months,
"quiet because starved," with the frozen-but-200 signature flagged for
attention.

**Follow-up:** internal cadence history (snapshot `computed_at` rows)
shows the vendor's monthly aggregate for month M lands ~mid-M+1 — May
data was live by 06-29, June landed 07-21/22. July is therefore due
~Aug 15 and **not overdue**; the fresh-pull/stalled-month combination
is the normal intra-month state. Watch trigger recorded: frontier
still at 202606 by ~Aug 22 → treat as a repeat of the July-2026
silent-cutover freeze.

### 🟡 Warning #3 — HIGH CVE on a pinned crypto library, unformalized (dependency-security check, day 4)

**Detected:** a HIGH advisory on the pinned `cryptography` 49.0.0, not
allowlisted, held at Warning by the check's self-feedback rule; the
report asked for "a decision either way."

**Follow-up:** the operator had already decided (2026-08-10: defer the
49→50 major). The allowlist PR (merged 2026-08-12) formalized it: an
Accepted-CVE allowlist row (not-reachable — the flaw is a
PKCS#7-decrypt oracle, and the codebase has zero PKCS#7 use; a
complete consumer inventory recorded after review caught an incomplete
first draft) with executable re-verify greps and a Remove-when scoped
to exactly this advisory. The mandated pre-commit review on the
allowlist edit also surfaced a real bug in the *weekly digest*:
deferral matching was package-name-only, so one documented deferral
would have muted every **future** advisory on that package — fixed
with per-entry advisory scoping plus a lockstep test tying deferral
entries to allowlist rows. The bump itself was deliberately NOT made.

### 🟡 Warning #4 — City D stale card (value-integrity check, day 2)

**Detected:** the stored card asserts a monthly volume metric of 21
(−66.5%) vs a raw value of 82; the check's own escalation rule
technically qualified (2 consecutive days) but the agent held at
Warning on a judgment call, citing the expected TTL self-heal — and
flagged the tension explicitly.

**Follow-up confirmed the judgment call to the hour:** the re-ingest
that corrected the raw value ran 08-11 06:03Z — *after* that morning's
card generation; the next signal run recomputed +30.8%, **below the
rule's 35% watch floor**, so the upsert-only runner correctly never
overwrites; the card self-expires on TTL 2026-08-14T05:05Z and the
finding clears by Friday's run. One bug found *in the docs*: the
rule docstring's opening line still said "fires ≥ 25%" five weeks
after the 2026-06-06 threshold raise — the stale figure misdirects
exactly this recompute (fixed in the 2026-08-12 fix train).

**Calibration note for the check:** "recompute drift persisting 2 days
→ Critical" should probably carve out the known-self-healing shape
(raw corrected *after* generation + recomputed deviation now
sub-threshold + unpublished/low-strength), which is distinguishable
from live drift using fields the check already reads.

### 🟢 Info #5 — City E mix-shift guard "gap" (cross-source validation check)

**Detected:** a genuine disagreement against the independent reference
index (−11.08% card vs +3.62% mix-adjusted) with
`mix_shift_suspect: false` — flagged as a possible emission-time guard
coverage gap.

**Follow-up:** not a guard bug — a band-boundary case. The stored
lineage shows a unit-price gap of 7.96 against
`DIVERGENCE_BAND_PCT = 8.0`: the guard missed by **0.04 points**. The
structural observation is that the band is an *absolute* gap and is
blind to *relative* divergence (−11.08 vs −3.12 is a 3.6× ratio — the
mix-shift fingerprint at small unit-price magnitudes). Deliberately
NOT changed inline: the band was backtest-calibrated (a backtest
analysis dated 2026-07-07), so re-calibration is a statistics exercise
with this case added to the sample. Layered defense held regardless —
the card was demoted to strength 2, below the outbound-distribution
floor.

### 🟢 Info #6 — error-tracker admin-notifier recurrence (error-triage check)

**The sleeper of the run.** Detected as a *below-threshold* Info: 8
events/24h, unassigned 7 weeks, "doesn't meet the Warning bar."

**Follow-up found a real customer-facing bug:** the newsletter
campaign dispatcher routes per-recipient sends through the admin
notifier under one alert class, sharing its **20/hour class-wide rate
cap**. The eligible recipient list is 28; every campaign's
delivered-event count in the database is **exactly 20** — the same ~8
cursor-order tail recipients received no campaign at all from
2026-06-22 until the fix. The error tracker's "8 events/24h" *was* the
per-campaign drop count, in plain sight, for seven weeks. All seven
recipient-override product-mail surfaces (newsletter, weekly digest,
team summary + invite, welcome, lifecycle) shared the latent cap. Fix
(merged 2026-08-12): per-recipient rate buckets for recipient-override
sends; the ops alert-storm protection unchanged.

**Calibration note for the check:** volume thresholds
(`events24h > 100`) rank *storms*, but a **steady low-volume
recurrence with a stable event count** (flat 8/day for weeks) is an
orthogonal signature — flat lines mean something *deterministic* is
failing at a fixed rate, which is exactly what a quota/cap bug looks
like. Worth a severity-table row (or at least a named heuristic) in
the error-triage check.

### 🟢 Info #7 / #9 / #10 — no action, per the report's own disposition

(One restated a known, documented gap in the run's own environment
setup; the CI-toolchain pin report was facts-only; and the daily
diff review's pattern-scan hits were downgraded because each carried
its own in-commit rationale.) The **advisory-identifier drift** on a second
pinned package (Info #8) was reconciled in the allowlist PR: the
advisory database confirms its PYSEC-style id aliases the
corresponding CVE / GHSA ids for the same flaw; the allowlist row now
matches the PYSEC id with a runtime-checkable alias-linkage guard.

## What this run demonstrates (the open-source pitch, honestly)

1. **The quiet-state detector worked end-to-end on its first real
   fire.** The content-freshness check exists because "working as
   intended" and "starved by a defect" look identical to green
   heartbeats; its guard stack + dwell escalation named 3 real starved
   markets and correctly called the cause class. The 2026-07-31 blind
   spot it was built from (documented in the check spec's own Coverage
   note) did not recur.
2. **Severity is a triage ranking, not a truth ranking.** The run's
   only *live customer-facing bug* was hiding under its lowest
   severity (Info #6), surfaced only because the report's discipline
   of "note it anyway, it's been unassigned 7 weeks" gave a follow-up
   session a thread to pull. Detectors buy you threads; diagnosis
   still has to pull them.
3. **Judgment calls against the letter of a rule need to be written
   down.** The value-integrity check's hold-at-Warning was explicitly
   flagged as a deviation with reasoning — which made it cheap to
   verify (it was right) and turned it into a calibration follow-up
   instead of an argument.
4. **Doc-mirror pins are load-bearing.** Every fix in this loop that
   touched a check spec, the catalog, the deferral list, or a runbook
   was forced into lockstep by the doc-mirror pin tests — the
   mechanism that keeps a documentation-heavy agent honest as code
   moves under it.
5. **Tier boundaries composed correctly.** The Tier-1 observer never
   acted; the follow-up session (full-autonomy, operator-directed) did
   all the acting, through the repo's normal review gates (3 PRs, each
   through a multi-agent adversarially-verified review; every
   regression pin verified red against pre-fix code).

## Outcome — five days later (verified 2026-08-16 against that morning's report)

Everything above was written the night of the fixes, so its claims were
*predictions*. The daily runs of 2026-08-12 through 2026-08-16 have
since scored them independently. Recorded here because a case study
whose predictions were never checked is marketing, not evidence.

**✅ Confirmed — Critical #1 (content-pipeline starvation).** The
2026-08-16 report's content-freshness check reads clean on both
dimensions: **0 of 35** qualifying markets flagged, and it names all
three formerly-starved markets as having published their 2026-06
story. The three recovered publication records are still
`status:"published"` in the live database with the attribution stamp
correctly absent (removed via the field-unset path, not blanked).
Fix + operator recovery both held.

**✅ Confirmed — Warning #3 (crypto-library deferral).** The
2026-08-16 report's dependency-security check suppresses 6 CVEs to
"Accepted", every row's re-verify greps re-run and passing, and it
names the 49→50 bump as "the operator's 2026-08-10 deliberate deferral
— no new action." Notably the second package's row now matches on its
PYSEC-style id — the alias reconciliation is doing exactly the job it
was added for, silently, five runs later.

**✅ Confirmed to the day — Warning #4 (City D).** The card expired
**2026-08-14T05:05Z**, exactly as the TTL arithmetic predicted, without
ever re-firing. The hold-at-Warning judgment call was correct.

⚠️ **But it re-surfaced, and that is a calibration finding.** The
2026-08-16 report flags the SAME card again at 285% claim-vs-raw drift
— because the raw value was revised again (82 → 81) while the card
still cites 21. The card has been expired for two days and no user can
see it. The value-integrity check's claim-vs-raw rubric has no
live/expired scoping, so an expired card can generate a Warning
indefinitely as raw data drifts away from it. Worth a spec decision:
scope the audit to live cards, or keep auditing expired ones but sever
them into their own (Info) severity band.

**◐ Partially confirmed — Info #6 (newsletter partial delivery).** The
error tracker is quiet: the 2026-08-16 report's error-triage check
reports **0 active issues** across both monitored projects, ending a
7-week recurrence. But the honest read is weaker than "fixed and
proven": the delivery-event records still show the **same 8 campaigns,
all capped at 20** (one at 19) — **no campaign has been sent since the
fix deployed**, so the delivery path has never been exercised against
a real 28-recipient list. The mechanism is verified by unit pins and
the recurrence stopping; the outcome is not yet observed. The next
campaign is the actual test.

**⚠️ Escalated — Info #5 (City E band-boundary).** The Tier-2 note
said: *"if this rule pattern recurs at strength ≥3 on a future card,
the guard has a coverage gap."* It recurred **twice in five days**.
The 2026-08-16 report surfaces City F (−6.22% vs reference +2.29%,
`mix_shift_suspect` false, unit-price gap **0.84**) and City G
(−6.16% vs +4.04%, gap **5.17**) — both strength 3, both with
unit-price gaps far inside the 8.0 band while diverging 8.5 and 10.2
points from the reference index. That is precisely the shape the
City E analysis named: an *absolute* divergence band is blind to
*relative* divergence.

The gap is now covered — by a different workstream. On 2026-08-14 an
emission-time cross-check against the reference index shipped
(proposal and implementation PRs, same day), and it stamped `demote`
on both of today's cards. It runs in **dark-run** (its enforce flag
unset), so the cards still emitted at strength 3; whether to enforce
is a live operator decision, and these two cards are evidence for it.

**The transferable lesson.** A calibration note that was deliberately
*not* acted on ("this is a backtest exercise, not an inline threshold
change") predicted a real failure mode that materialized within five
days — and the fix arrived from an unrelated thread that had reached
the same conclusion from a different direction. Writing down the
structural observation, separately from the decision to act on it, is
what let two independent investigations converge instead of collide.

## Candidate ledger rows (operator to confirm — the agent never self-certifies)

Per the calibration-ledger governance, these are **proposals** for the
operator / weekly bookkeeper, not entries. **Status 2026-08-16: still
unconfirmed** — the ledger holds 2 rows and its tally reads 2/3 toward
the Tier-2 gate. The mechanism is a `TP`/`FP` comment (plus a one-line
reason) on the daily QA report; the Monday ledger-retro bookkeeper
then proposes the row in its sign-off issue for the operator to paste.

| Date | Check | Candidate verdict |
|---|---|---|
| 2026-08-11 | Content freshness (per-market Critical) | TP — pipeline defect confirmed (the 2026-07-21 bulk-cleanup tombstones), fixed 2026-08-12; first dwell-escalation fire, correct on all counts; independently confirmed clean by the 2026-08-16 report |
| 2026-08-11 | Error triage (Info #6) | TP — below-threshold Info turned out to be a 7-week partial-delivery bug (28→20 recipients per campaign), fixed 2026-08-12; the recurrence ended, end-to-end delivery still unexercised |
| 2026-08-11 | Value integrity (Warning #4) | TP-and-self-healed — drift real, hold-at-Warning judgment validated (card expired 08-14 exactly as predicted) |
| 2026-08-11 | Cross-source validation (Info #5) | TP (calibration-grade) — real reference-index disagreement; guard per-spec; the 0.04pt band-boundary note predicted the strength-3 recurrences the 2026-08-16 report surfaced 5 days later |

## Session mechanics (for reproducibility)

Follow-up session, 2026-08-12 ~00:30–04:00Z: live-database diagnosis
via a read-only connection; three PRs (content-pipeline tombstone
semantics · CVE allowlist + digest advisory-scoping · per-recipient
notify buckets + aggregate backstop — all merged 2026-08-12), each
through a fanned-out adversarial code review (6 / 6 / 7 findings
respectively, all confirmed ones fixed in-diff pre-merge — the
notifier PR's review notably killed the naive first cut, which would
have opened an ~18k/hr spam-relay vector on the team-invite endpoint:
the gate working exactly as designed); full backend suite green on
every branch; fail-on-main verification on every new regression pin.
Disposition comment posted back to the daily report of 2026-08-11.
