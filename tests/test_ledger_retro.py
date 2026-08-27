"""Behavioral pins for template/.github/scripts/qa_ledger_retro.py.

The weekly-retro bookkeeper proposes calibration-ledger rows from operator
TP/FP comments on the daily `[qa-agent]` issues.  Three properties are
load-bearing enough to pin:

* **It never self-certifies.**  The operator allowlist / default bot
  exclusion is the guard that keeps the agent from grading its own findings —
  the entity whose autonomy is being measured must not hold the pen on the
  measurement (docs/calibration_ledger.md).
* **It renders in the ledger's exact column order and dedupes against rows
  already recorded.**  The sign-off issue says "paste verbatim", so a wrong
  column order corrupts the ledger (a 2026-07-22 production review caught the
  retro rendering Verdict where the ledger has Finding — TP/FP would have
  landed under the column the promotion tally reads), and a re-proposed
  hand-recorded row would double-count one finding toward the ≥3-TP gate.
* **A comment may carry several verdicts.**  Found 2026-08-18 via a weekly
  retro's undercount: one operator comment held two TPs on ONE line; the old
  parser kept the first, swallowed the second into the first row's Finding
  cell, and silently stalled the promotion gate.  The multi-verdict scan is
  guarded both ways — it must split real verdicts and must NOT mint rows from
  prose that merely mentions "TP/FP".
"""

import json
import os
import re
import subprocess
import sys

from probes import LEDGER_DOC, LEDGER_SCRIPT, load_script_module

retro = load_script_module(LEDGER_SCRIPT, "qa_ledger_retro")


def _issue(number, title, comments, created_at="2026-06-20T10:00:00Z"):
    return {"number": number, "title": title, "createdAt": created_at,
            "comments": [{"author": {"login": a}, "body": b} for a, b in comments]}


# ── The basic parse ──────────────────────────────────────────────────────────
def test_extracts_tp_and_fp_verdicts_with_dates_and_checks():
    issues = [
        _issue(40701, "[qa-agent] 2026-06-24 morning report",
               [("operator-1", "TP — Check 2 caught a real error-tracker 500 spike")]),
        _issue(40702, "[qa-agent] 2026-06-25 morning report",
               [("operator-1", "FP noise, the cert check flapped")]),
    ]
    rows = retro.build_proposed_rows(issues)
    assert [r["verdict"] for r in rows] == ["TP", "FP"]
    assert rows[0]["date"] == "2026-06-24" and rows[0]["issue"] == 40701
    assert rows[0]["check"] == "Check 2"
    assert rows[1]["check"] == "—"  # no check reference in the FP comment


def test_non_verdict_comments_and_boundary_words_are_ignored():
    # "looking into..." has no verdict; "TPS" is not the TP token (word
    # boundary) — a scan that matched it would mint rows from prose.
    issues = [_issue(40703, "[qa-agent] 2026-06-25 report",
                     [("operator-1", "looking into this one"),
                      ("operator-1", "TPS report incoming")])]
    assert retro.build_proposed_rows(issues) == []


def test_operator_allowlist_and_default_bot_exclusion_block_self_certification():
    """A TP from a bot must never become a ledger row: with an explicit
    allowlist only listed logins count, and with NO allowlist `[bot]`
    accounts are still excluded — in BOTH verdict shapes, because a bot
    writing `verdict: TP` is just as much self-certification."""
    issues = [_issue(40704, "[qa-agent] 2026-06-25 report",
                     [("github-actions[bot]", "TP — looks real to me"),
                      ("operator-1", "FP — actually a flake")])]
    rows = retro.build_proposed_rows(issues, operator_logins={"operator-1"})
    assert [(r["verdict"], r["author"]) for r in rows] == [("FP", "operator-1")]

    issues = [_issue(40720, "[qa-agent] 2026-06-25 report",
                     [("github-actions[bot]", "TP — self-graded"),
                      ("github-actions[bot]", "Automated summary — verdict: TP"),
                      ("operator-1", "TP — real, Check 3")])]
    rows = retro.build_proposed_rows(issues)
    assert len(rows) == 1 and rows[0]["author"] == "operator-1" and rows[0]["check"] == "Check 3"


def test_date_falls_back_to_created_at_and_bare_verdict_gets_a_reason():
    issues = [_issue(40705, "[qa-agent] morning report (undated)",
                     [("operator-1", "TP")], created_at="2026-06-19T09:00:00Z")]
    row = retro.build_proposed_rows(issues)[0]
    assert row["date"] == "2026-06-19"
    assert row["reason"] == "(no reason given)"


def test_reason_pipe_escaped_and_empty_state_renders_instructions():
    issues = [_issue(40730, "[qa-agent] 2026-06-25 report",
                     [("operator-1", "FP — flapped on a|b parsing")])]
    out = retro.render_markdown(retro.build_proposed_rows(issues))
    assert r"a\|b" in out  # a stray | would add a phantom column on paste
    empty = retro.render_markdown([])
    assert "No new operator" in empty and "TP" in empty and "FP" in empty


def test_render_tally_and_the_ledgers_own_column_order():
    """Finding 4th, Verdict 5th, author folded into Notes — the ledger's
    order, because the sign-off issue says "paste verbatim" and any other
    order corrupts the column the promotion tally reads."""
    issues = [_issue(40710, "[qa-agent] 2026-06-24 report",
                     [("operator-1", "TP — Check 5 real"),
                      ("operator-1", "TP — Check 8 real"),
                      ("operator-1", "FP flake")])]
    out = retro.render_markdown(retro.build_proposed_rows(issues))
    assert "2 TP · 1 FP" in out
    assert retro.LEDGER_HEADER in out
    assert "| 2026-06-24 | #40710 | Check 5 | Check 5 real | TP | by @operator-1 |" in out
    assert "Tier-2 gate" in out
    assert "operator sign-off required" in out


def test_ledger_header_is_derived_from_the_ledger_doc():
    """LEDGER_HEADER must byte-match the ledger table's actual header line —
    derived from the doc so the two can never drift apart (the drift WAS the
    2026-07-22 defect: the retro's own six columns in a different order)."""
    doc_header = next(
        line for line in LEDGER_DOC.read_text(encoding="utf-8").splitlines()
        if line.startswith("| Date |")
    )
    assert retro.LEDGER_HEADER == doc_header


# ── Dedupe against the recorded ledger ───────────────────────────────────────
def test_parse_ledger_pairs_reads_rows_and_ignores_scaffolding():
    text = "\n".join([
        "Prose mentioning #40999 and Check 3 outside the table must not count.",
        "| Date | QA Issue | Check | Finding (1-line) | Verdict | Notes |",
        "|---|---|---|---|---|---|",
        "| _(empty — none yet)_ | | | | | |",
        "| 2026-07-22 | #40957 | Check 16 | six cities ~3x on one flow metric | TP | operator-confirmed |",
        "| 2026-07-23 | #40970 | — | row without a check ref | FP | noise |",
    ])
    assert retro.parse_ledger_pairs(text) == {(40957, "Check 16"), (40970, "—")}


def test_shipped_ledger_examples_cannot_poison_the_dedupe():
    """The shipped docs/calibration_ledger.md holds an empty live table plus
    illustrative example rows keyed `#NNN` — deliberately not a real issue
    number, so the parser must extract NOTHING from the shipped file.  If an
    example row ever gained a digit issue number, the retro would silently
    treat a real future verdict as already-recorded."""
    assert retro.parse_ledger_pairs(LEDGER_DOC.read_text(encoding="utf-8")) == set()


def test_dedupe_keys_on_the_issue_check_pair_and_lists_skipped_rows():
    """A recorded row is excluded from the paste table (its tally must not
    count toward the gate a second time) but is visibly listed as
    already-recorded; the key is the (issue, check) PAIR, so one daily issue
    can still yield a second row for a different check — and when every row
    is recorded, no paste table renders at all."""
    issues = [
        _issue(40957, "[qa-agent] 2026-07-22 morning report",
               [("operator-1", "TP — Check 16 real, an upstream data artifact")]),
        _issue(40958, "[qa-agent] 2026-07-23 morning report",
               [("operator-1", "TP — Check 2 real error-tracker spike")]),
    ]
    rows = retro.build_proposed_rows(issues, recorded={(40957, "Check 16")})
    assert [r["already_recorded"] for r in rows] == [True, False]
    out = retro.render_markdown(rows)
    assert "1 TP · 0 FP" in out            # tally counts NEW rows only
    assert "| 2026-07-23 | #40958 |" in out  # the new row is pasteable
    assert "| 2026-07-22 | #40957 |" not in out
    assert "do not paste" in out and "#40957" in out

    # Same issue, different check → still proposed.
    other = [_issue(40957, "[qa-agent] 2026-07-22 report",
                    [("operator-1", "FP — Check 7 flake")])]
    assert retro.build_proposed_rows(other, recorded={(40957, "Check 16")})[0][
        "already_recorded"] is False

    # Every row recorded → no header, no paste table, only the skip list.
    all_dup = retro.render_markdown(
        retro.build_proposed_rows(
            [_issue(40957, "[qa-agent] 2026-07-22 report", [("operator-1", "TP — Check 16 real")])],
            recorded={(40957, "Check 16")},
        ))
    assert "already recorded" in all_dup and "do not paste" in all_dup
    assert retro.LEDGER_HEADER not in all_dup


# ── Shape 2: the `verdict:` marker ───────────────────────────────────────────
def test_verdict_marker_line_shape_with_long_tokens_and_scoped_reason():
    """The operator's real 2026-07-22 verdict did not START with TP — it was a
    heading ending `— verdict: TRUE POSITIVE, …`, invisible to shape 1.  The
    marker keeps shape 2 deliberate; the reason is the rest of THAT line, not
    the whole comment; and a check named BEFORE the marker still attaches."""
    body = ("## Check 16 Critical — verdict: TRUE POSITIVE, root cause corrected "
            "— an upstream vendor artifact, not the suspected internal bug.\n\n"
            "Evidence: metro-level June-2026 values diverged sharply.")
    rows = retro.build_proposed_rows(
        [_issue(40957, "[qa-agent] 2026-07-22 morning report", [("operator-1", body)])])
    assert len(rows) == 1
    assert rows[0]["verdict"] == "TP" and rows[0]["check"] == "Check 16"
    assert rows[0]["reason"].startswith("root cause corrected")
    assert "Evidence" not in rows[0]["reason"]

    fp = retro.build_proposed_rows(
        [_issue(40958, "[qa-agent] 2026-07-23 report",
                [("operator-1", "Investigated overnight. Verdict: FALSE POSITIVE "
                         "— the cert check flapped again.")])])
    assert fp[0]["verdict"] == "FP" and "cert check flapped" in fp[0]["reason"]


def test_verdict_marker_without_a_token_and_bare_tp_prose_yield_nothing():
    # Neither "verdict:" followed by a non-token, nor prose merely containing
    # "TP", may count — widening the matcher must not loosen the anchor guard.
    issues = [_issue(40959, "[qa-agent] 2026-07-24 report",
                     [("operator-1", "I think the TP count is off; verdict: pending more data."),
                      ("operator-1", "The word verdict appears here but TP is elsewhere.")])]
    assert retro.build_proposed_rows(issues) == []


# ── Multi-verdict comments (the 2026-08-18 undercount class) ─────────────────
_TWO_TP = (
    "TP — Check 5: real pipeline defect (mass-skip tombstones), fix shipped, "
    "confirmed clean by a follow-up run. TP — Check 2: a below-threshold Info "
    "was a 7-week partial-delivery bug, fixed."
)

_BATCH_CLOSE = (
    "Batch-close (operator-approved ledger cleanup): headline findings "
    "superseded or resolved — see the current-state issue and this issue's "
    "disposition comments where present. TP/FP calibration verdicts are still "
    "owed and may be added here after close (the retro scans closed issues too)."
)


def test_multi_verdict_comment_yields_one_row_per_verdict_with_scoped_reasons():
    """Two verdicts on ONE line: each gets its own row, attributed to ITS OWN
    check, and neither reason bleeds into the other (the pre-fix DOTALL let
    verdict 1's reason run to end-of-body, swallowing verdict 2 — token and
    all — into the first row's Finding cell)."""
    rows = retro.build_proposed_rows(
        [_issue(1117, "[qa-agent] 2026-08-11 morning report", [("operator-1", _TWO_TP)])])
    assert [(r["verdict"], r["check"]) for r in rows] == [
        ("TP", "Check 5"), ("TP", "Check 2")]
    first, second = rows
    assert "mass-skip tombstones" in first["reason"]
    assert "partial-delivery" not in first["reason"]
    assert "TP —" not in first["reason"]  # the second verdict's own token
    assert "partial-delivery" in second["reason"]
    assert "2 TP · 0 FP" in retro.render_markdown(rows)
    # Dedupe stays per-(issue, check): a comment's two verdicts are
    # independently recordable.
    rows = retro.build_proposed_rows(
        [_issue(1117, "[qa-agent] 2026-08-11 report", [("operator-1", _TWO_TP)])],
        recorded={(1117, "Check 5")})
    assert [r["already_recorded"] for r in rows] == [True, False]


def test_mixed_tp_and_fp_are_typed_independently():
    """An FP carries different ledger consequences than a TP (a Critical FP
    resets the clean-day clock), so a mixed comment must not collapse both
    verdicts to whichever came first."""
    body = ("TP — Check 9: the stuck-job alarm was real. "
            "FP — Check 7: working as designed, the pin was current.")
    rows = retro.build_proposed_rows(
        [_issue(1141, "[qa-agent] 2026-08-13 morning report", [("operator-1", body)])])
    assert [(r["verdict"], r["check"]) for r in rows] == [
        ("TP", "Check 9"), ("FP", "Check 7")]
    assert "1 TP · 1 FP" in retro.render_markdown(rows)


def test_shape2_multiple_markers_each_yield_a_row_even_on_one_line():
    """Shape 2's twin of the same defect: a reason group running to
    end-of-line eats a second marker sharing the line, so finditer never sees
    it.  Losing the ATTRIBUTION there is acceptable; losing the VERDICT is
    not.  And two markers on separate heading lines each attach to the check
    named before them."""
    two_lines = ("## Check 5 Critical — verdict: TRUE POSITIVE, the resolver fallback broke.\n"
                 "## Check 9 Warning — verdict: FALSE POSITIVE, the cert check flapped.")
    rows = retro.build_proposed_rows(
        [_issue(40980, "[qa-agent] 2026-07-30 report", [("operator-1", two_lines)])])
    assert [(r["verdict"], r["check"]) for r in rows] == [
        ("TP", "Check 5"), ("FP", "Check 9")]
    assert "cert check" not in rows[0]["reason"]

    one_line = "Check 5 — verdict: TP, real. Check 9 — verdict: FP, flake."
    rows = retro.build_proposed_rows(
        [_issue(40981, "[qa-agent] 2026-07-31 report", [("operator-1", one_line)])])
    assert [r["verdict"] for r in rows] == ["TP", "FP"]
    assert "flake" in rows[1]["reason"] and "flake" not in rows[0]["reason"]


def test_prose_mentions_and_abbreviations_do_not_mint_or_split_verdicts():
    """The anchor guards, all three: a comment that does not OPEN with a
    verdict proposes nothing even when "TP/FP" starts a sentence mid-prose
    (the real batch-close shape); a verdict comment that later mentions
    "TP/FP" in prose stays ONE verdict (no separator after the token); and a
    sentence ending in an abbreviation must not turn the next word into a
    phantom second verdict ("…shipped in Aug. TP tallies are unaffected.")."""
    assert retro.build_proposed_rows(
        [_issue(1117, "[qa-agent] 2026-08-11 report", [("operator-1", _BATCH_CLOSE)])]) == []

    mention = ("TP — Check 3: the freshness banner was genuinely stale. "
               "TP/FP bookkeeping for this one is already tracked elsewhere.")
    rows = retro.build_proposed_rows(
        [_issue(1150, "[qa-agent] 2026-08-14 report", [("operator-1", mention)])])
    assert len(rows) == 1 and rows[0]["check"] == "Check 3"

    abbrev = "TP — Check 5: real regression, shipped in Aug. TP tallies are unaffected."
    rows = retro.build_proposed_rows(
        [_issue(40982, "[qa-agent] 2026-08-01 report", [("operator-1", abbrev)])])
    assert len(rows) == 1
    assert "tallies are unaffected" in rows[0]["reason"]


def test_line_initial_verdict_after_a_sentence_end_still_splits():
    """The two scan branches overlap at a line break following a sentence
    ("real.\\nFP flake"): the sentence branch wins the alternation, so a
    match-start-based line test would misread a line-initial verdict as
    mid-line and drop it for want of punctuation.  A bare "FP flake" line is
    a documented verdict shape and must survive — indented too."""
    rows = retro.build_proposed_rows(
        [_issue(40983, "[qa-agent] 2026-08-02 report", [("operator-1", "TP — Check 5: real.\nFP flake")])])
    assert [r["verdict"] for r in rows] == ["TP", "FP"]
    assert rows[1]["reason"] == "flake"
    indented = retro.build_proposed_rows(
        [_issue(40984, "[qa-agent] 2026-08-02 report",
                [("operator-1", "TP — Check 5: real.\n   FP flake")])])
    assert [r["verdict"] for r in indented] == ["TP", "FP"]


# ── No-regression goldens: single-verdict parses are byte-identical ──────────
def test_single_verdict_render_goldens_both_shapes():
    """The multi-verdict split's main regression risk is changing what a
    single-verdict comment renders as — pinned byte-for-byte in both shapes,
    including the 160-char reason cap."""
    body1 = ("TP — Check 5: the error-tracker 500 spike on the markets API was a "
             "real regression in the resolver fallback, not noise.")
    rows = retro.build_proposed_rows(
        [_issue(40710, "[qa-agent] 2026-06-24 report", [("operator-1", body1)])])
    assert len(rows) == 1
    assert retro.render_markdown(rows).splitlines()[-1] == (
        "| 2026-06-24 | #40710 | Check 5 | Check 5: the error-tracker 500 spike "
        "on the markets API was a real regression in the resolver fallback, "
        "not noise. | TP | by @operator-1 |"
    )
    body2 = ("## Check 16 Critical — verdict: TRUE POSITIVE, root cause corrected — "
             "an upstream vendor artifact.\n\nEvidence: June-2026 values diverged.")
    rows = retro.build_proposed_rows(
        [_issue(40710, "[qa-agent] 2026-06-24 report", [("operator-1", body2)])])
    assert retro.render_markdown(rows).splitlines()[-1] == (
        "| 2026-06-24 | #40710 | Check 16 | root cause corrected — an upstream "
        "vendor artifact. | TP | by @operator-1 |"
    )
    long = "TP — Check 5: " + "x" * 300
    row = retro.build_proposed_rows(
        [_issue(40711, "[qa-agent] 2026-06-24 report", [("operator-1", long)])])[0]
    assert len(row["reason"]) == 160


# ── The script entrypoint honors QA_LEDGER_PATH ──────────────────────────────
def test_main_reads_stdin_and_the_qa_ledger_path_override(tmp_path):
    """The workflow pipes `gh issue list --json` output into the script and
    the ledger is read from QA_LEDGER_PATH (default: the public
    docs/calibration_ledger.md relative to the checkout root).  Run the real
    entrypoint from a foreign cwd with the override pointing at a synthetic
    ledger: the recorded pair must dedupe, the new one must render — proving
    both the stdin plumbing and the override."""
    ledger = tmp_path / "ledger.md"
    ledger.write_text(
        "| Date | QA Issue | Check | Finding (1-line) | Verdict | Notes |\n"
        "|---|---|---|---|---|---|\n"
        "| 2026-07-22 | #40957 | Check 16 | already recorded by hand | TP | operator |\n",
        encoding="utf-8",
    )
    issues = [
        _issue(40957, "[qa-agent] 2026-07-22 morning report",
               [("operator-1", "TP — Check 16 real")]),
        _issue(40958, "[qa-agent] 2026-07-23 morning report",
               [("operator-1", "TP — Check 2 real")]),
    ]
    env = {k: v for k, v in os.environ.items() if k != "QA_LEDGER_OPERATORS"}
    env["QA_LEDGER_PATH"] = str(ledger)
    proc = subprocess.run(
        [sys.executable, str(LEDGER_SCRIPT)],
        input=json.dumps(issues), capture_output=True, text=True,
        timeout=30, cwd=tmp_path, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "| 2026-07-23 | #40958 | Check 2 |" in out
    assert "| 2026-07-22 | #40957 |" not in out
    assert "do not paste" in out and "#40957" in out
    assert "1 TP · 0 FP" in out


def test_check_reference_regex_tolerates_the_documented_spellings():
    """`Check 16`, `check 16`, `Check #16` all attribute; a bare number does
    not.  The ledger doc tells the operator "optionally `Check N`" — the
    parser must accept what the doc teaches."""
    for spelling in ("Check 16", "check 16", "Check #16"):
        rows = retro.build_proposed_rows(
            [_issue(40990, "[qa-agent] 2026-08-03 report",
                    [("operator-1", f"TP — {spelling}: real")])])
        assert rows[0]["check"] == "Check 16", spelling
    rows = retro.build_proposed_rows(
        [_issue(40991, "[qa-agent] 2026-08-03 report", [("operator-1", "TP — 16 was real")])])
    assert rows[0]["check"] == "—"
