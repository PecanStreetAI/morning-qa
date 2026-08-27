#!/usr/bin/env bash
#
# Classify a morning-QA report for labeling.  Consumed by the
# "Post / update daily QA Issue" step of .github/workflows/morning-qa.yml.
#
#   usage:  qa_severity_label.sh <report-path>
#   stdout: exactly two lines —
#             is_critical=0|1
#             is_incomplete=0|1
#   exit:   ALWAYS 0 (see "Fail-soft" below)
#
# ── Why this is a checked-in script and not inline bash ──────────────────
#
# This classifier had THREE bugs while living inside the workflow's
# `run:` block, each of which shipped silently because nothing could execute
# it in isolation:
#
#   * 2026-06-03 — grepped for the "🔴 Critical" section HEADER, which every
#     report carries (a clean day prints "_None today._" underneath), so the
#     entire qa-agent-daily backlog got stamped priority:critical.
#   * 2026-07-15 — the label was ADD-only, so a day first posted critical kept
#     the label after a later re-run produced a clean report (confirmed live
#     on a clean-report day still wearing it).
#   * 2026-08-06 — the in-progress skeleton was SPECIFIED to carry
#     `qa-max-severity: none`, so a run that completed ZERO of its checks
#     before the API dropped posted a report byte-indistinguishable from a
#     clean full-roster day: no label, benign-looking operator email.  That
#     is the "unavailable ≠ clean" class recurring at the report envelope
#     instead of inside a check.
#
# Same reasoning (and the same directory) as qa_run_telemetry.js, whose own
# pin says the parser "must stay a checked-in script, never an inline
# `node -e` payload — untestable + bash-single-quote copy-edit hazard".
# Behavior here should be pinned by a test that runs THIS FILE against
# synthetic report bodies — including a verbatim copy of a zero-checks-run
# body — rather than asserting that some grep string still appears in the
# YAML.  (A pin that only mirrors the source proves agreement, not
# correctness — a lesson from a 2026-08-04 review.)
#
# ── The two axes ─────────────────────────────────────────────────────────
#
# SEVERITY ("what did we find") and COMPLETENESS ("did we look at all") are
# INDEPENDENT.  Collapsing them into one marker is what produced the
# 2026-08-06 bug, so they are reported as two separate booleans:
#
#   is_critical   → priority:critical          (a finding needs triage)
#   is_incomplete → qa-agent-incomplete        (coverage is unknown)
#
# Both can be 1 at once: a run that found a Critical and THEN died has a real
# finding AND unestablished coverage.
#
# ── Fail-soft ────────────────────────────────────────────────────────────
#
# Always exits 0.  A classifier that hard-fails would take down the daily
# issue post — the one artifact this whole workflow exists to produce — over
# a labeling decision.  The safe default when we cannot read the report is
# is_incomplete=1: we must never assert "clean" about a report we could not
# examine.  That is the "unavailable ≠ clean" rule applied to this script
# itself.

set -u

REPORT="${1:-}"

is_critical=0
is_incomplete=0

# Missing / empty / unreadable report.  In practice the post-issue job's
# "Synthesize fallback report if artifact is missing" step guarantees a
# non-empty file before we run, so this is a defensive path — but it must
# resolve to "cannot establish coverage", never to a silent clean.
if [ -z "$REPORT" ] || [ ! -r "$REPORT" ] || [ ! -s "$REPORT" ]; then
  printf 'is_critical=%s\n' "$is_critical"
  printf 'is_incomplete=%s\n' 1
  exit 0
fi

# ── The header region ────────────────────────────────────────────────────
#
# BOTH machine-read fields (the marker and the Status line) live in the
# report's header, and they are matched ONLY there.  This is load-bearing,
# not tidiness: the report BODY is free-form LLM output that routinely
# quotes these very strings.  A diff-review check that prints landed commit
# subjects verbatim — under a commit convention that enumerates every
# change — will put the literal text "qa-max-severity: unknown" into a
# report body the day a change to this script lands.
#
# Scanning the whole file (as the pre-extraction inline greps did) breaks in
# all three directions, each verified by running this script:
#   * FALSE INCOMPLETE — a clean full-roster day whose diff-review section
#     quotes a commit subject mentioning the marker gets stamped
#     qa-agent-incomplete, discrediting the label on the first day it ships.
#   * FALSE CRITICAL — a body quoting `qa-max-severity: critical` (a
#     shadow-draft diff, a commit subject) stamps priority:critical on a
#     clean day, which the supersede step then pins open forever.  That is
#     the 2026-06-03 alarm-fatigue bug re-entered by a different door.
#   * FALSE NEGATIVE, the dangerous one — a marker-LESS report (the "older
#     skill / agent slip" case handled below) whose body quotes any marker
#     takes the marker-present branch and never reaches the legacy headline
#     fallback, so a genuine 3-Critical day gets no label and is auto-closed
#     by the next morning's supersede step.
#
# 12 lines covers the documented header block (marker, H1, Status, Run,
# Tier, Commit, and the "## Headline" line) with room to spare, and cannot
# reach the findings or check-results sections where quoted text lives.
HEADER=$(head -n 12 "$REPORT" 2>/dev/null || true)

# ── Severity ─────────────────────────────────────────────────────────────
#
# The `<!-- qa-max-severity: ... -->` marker the skill writes as the report's
# FIRST line is the source of truth (SKILL.md § Output protocol).  The
# recognized vocabulary here and SKILL.md's marker table must be pinned in
# lockstep — a value present in one but not the other would silently fall
# through to the legacy path below.
if printf '%s\n' "$HEADER" | grep -qiE 'qa-max-severity:[[:space:]]*(unknown|none|info|warning|critical)'; then
  # Marker present — authoritative.
  if printf '%s\n' "$HEADER" | grep -qiE 'qa-max-severity:[[:space:]]*critical'; then
    is_critical=1
  fi
  # `unknown` = the run is still in progress and has established nothing.
  # It exists so the in-progress skeleton stops CLAIMING `none`; `none` is
  # now legal only on a ✅ Complete report.
  if printf '%s\n' "$HEADER" | grep -qiE 'qa-max-severity:[[:space:]]*unknown'; then
    is_incomplete=1
  fi
else
  # Marker missing (an older skill revision, or an agent slip).  Fall back to
  # a NON-ZERO Critical count in the headline — deliberately unchanged from
  # the pre-extraction behavior.  It must not match the always-present empty
  # "### 🔴 Critical" header, which is the 2026-06-03 bug.
  #
  # This one probe scans the WHOLE file rather than $HEADER, on purpose: a
  # marker-less report is by definition off-spec, so its headline cannot be
  # assumed to sit in the header block, and for the legacy path the safer
  # miss is a false Critical (operator reads a clean report) over a missed
  # one.  Deliberately asymmetric with the marker probes above, where the
  # spec DOES pin the location and body quotes are the live risk.
  if grep -qE '[1-9][0-9]*[[:space:]]+Critical\b' "$REPORT" 2>/dev/null; then
    is_critical=1
  fi
  # NOTE, deliberate: a missing marker alone does NOT set is_incomplete.  A
  # marker-less report that says "✅ Complete" really is complete, and
  # flagging every legacy-shaped report incomplete would manufacture a new
  # false-positive class.  The status-line probe below is the independent
  # completeness signal and covers the case that matters.
fi

# ── Completeness ─────────────────────────────────────────────────────────
#
# Independent of the marker, and load-bearing: fixing only the skeleton's
# marker would MOVE the 2026-08-06 bug rather than close it.  An agent that
# complies with SKILL.md step 2 (rewrite after every check) and then dies
# after three clean checks writes `none` + "⏳ In progress (3/N)" — benign
# marker, aborted run.  A report that still says "In progress" at post time
# means the run never reached its final write.
#
# Anchored to the Status LINE, not a bare "In progress" substring, so a
# finding that happens to mention an in-progress migration cannot trip it.
# Deliberately does NOT match the ⏳ emoji: the guard must not rest on a
# multi-byte literal surviving future copy-edits or a locale change.
#
# Tolerant of two formatting variances, both of which would otherwise let an
# aborted run through as clean — the dangerous direction:
#   * LEADING WHITESPACE.  SKILL.md's step-1 skeleton lives inside a numbered
#     list, so the template the agent copies from is itself indented three
#     spaces; an agent that reproduces that indentation would defeat a
#     column-0 anchor.  (Verified reachable, 2026-08-06.)
#   * OPTIONAL BOLD.  `**Status:**` is what SKILL.md specifies, but a bare
#     `Status:` is the same field and must classify the same way.
# Still a LINE anchor, so ordinary prose cannot match — "…the migration is
# in progress" mid-sentence does not, nor does a `> Status: …` blockquote.
#
# Scoped to $HEADER for the reason given above: a report whose BODY quotes a
# skeleton (a shadow draft, a quoted commit subject, a findings excerpt)
# must not flip a completed run to incomplete.  The Status line is
# specified to sit in the header block, so bounding it there costs nothing.
if printf '%s\n' "$HEADER" | grep -qiE '^[[:space:]]*\*{0,2}Status:\*{0,2}.*In progress'; then
  is_incomplete=1
fi

printf 'is_critical=%s\n' "$is_critical"
printf 'is_incomplete=%s\n' "$is_incomplete"
exit 0
