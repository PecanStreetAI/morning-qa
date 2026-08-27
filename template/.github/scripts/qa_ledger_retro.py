"""
qa_ledger_retro.py — propose calibration-ledger rows from the week's QA issues.

The "weekly-retro bookkeeper" the calibration ledger anticipates
(docs/calibration_ledger.md).  It reads recent `[qa-agent]` issues + the
operator's TP/FP comments and PROPOSES ledger rows for sign-off.  It NEVER edits
the ledger and NEVER self-certifies a finding — the operator's comment is the
authority; self-certification would defeat the Tier-2 promotion gate.

This module is pure parsing + rendering — its only I/O is reading the local
calibration ledger, to dedupe proposals against rows already recorded there
(2026-07-22: a finding was hand-recorded ahead of the retro; re-proposing it
would have double-counted one finding toward the ≥3-TP gate).  No network, so
it's unit-testable.  The workflow (`.github/workflows/qa-ledger-retro.yml`)
fetches the issues via `gh` and pipes them in, then posts the proposed rows as
a `qa-ledger-retro` sign-off issue.  It is invoked as a plain script from the
repo checkout root, which is what the relative ledger path below assumes
(override with QA_LEDGER_PATH).
"""
from __future__ import annotations

import re
from typing import Optional

# Ledger table shape.  MUST byte-match the table header in
# docs/calibration_ledger.md: the sign-off issue tells the operator to paste
# approved rows verbatim, so any other column order here corrupts the ledger —
# a 2026-07-22 review caught the retro rendering Verdict 4th where the ledger
# has Finding 4th, which would have put TP/FP under Finding and broken the
# column the promotion tally reads.  Keep the two pinned together by a test
# that derives this header from the ledger doc.
LEDGER_HEADER = "| Date | QA Issue | Check | Finding (1-line) | Verdict | Notes |"
LEDGER_SEPARATOR = "|---|---|---|---|---|---|"

# An operator verdict comment — two documented shapes (both still gated by the
# non-bot / operator-allowlist check, so the agent can never self-certify):
#   1. The body STARTS with TP or FP, optionally followed by a separator +
#      reason: "TP — Sentry caught a real 500 spike".
#   2. Any line containing a literal `verdict:` marker followed by the token —
#      "## Check 16 Critical — verdict: TRUE POSITIVE, root cause corrected …"
#      (the shape the operator's actual 2026-07-22 verdict used, which shape 1
#      was blind to).
# The `verdict:` marker keeps shape 2 deliberate: bare "TP" inside prose still
# never matches, so widening the matcher did not loosen the body-anchor guard.
#
# EITHER SHAPE MAY CARRY SEVERAL VERDICTS (found 2026-08-18 via a weekly
# retro's undercount).  One operator comment was ONE line holding two:
# "TP — Check 15: … confirmed clean by a follow-up fix. TP — Check 2: …".  The
# old shape-1 pattern was `^\s*(TP|FP)\b[\s:.—-]*(.*)$` under DOTALL, and the
# row builder appended exactly once per comment — so the second verdict was
# BOTH dropped from the TP tally (Tier-2 promotion gate #2 is ≥ 3 confirmed
# TPs, so undercounting stalls promotion invisibly) AND swallowed into the
# first row's Finding cell by DOTALL's run-to-end-of-body.  Pasting that row
# would have corrupted the ledger, not merely undercounted it.
#
# Hence the scan below is anchored at a line start OR after a sentence terminator
# — line-anchoring alone would have missed the live instance, whose two verdicts
# share one line.  It is deliberately NOT a free-floating search: see
# `_shape1_occurrences` for the entry guard that keeps prose from manufacturing
# rows.  Group 2 is the separator, which the guard inspects.
_VERDICT_SCAN_RE = re.compile(
    r"(?:^|(?<=[.!?])\s)[ \t]*(TP|FP)\b([\s:.—-]*)",
    re.IGNORECASE | re.MULTILINE,
)
# Group 2 is tempered-greedy — "rest of the line, but stop before another
# `verdict:` marker".  A plain `[^\n]*` runs to end-of-line, which is shape 2's
# own copy of the shape-1 DOTALL bug: two markers on ONE line and the second is
# eaten by the first's reason, so finditer never sees it.
_VERDICT_LINE_RE = re.compile(
    r"\bverdict:\s*(TP|FP|TRUE POSITIVE|FALSE POSITIVE)\b[\s:,.—-]*"
    r"((?:(?!\bverdict:)[^\n])*)",
    re.IGNORECASE,
)
_VERDICT_TOKENS = {
    "TP": "TP", "TRUE POSITIVE": "TP",
    "FP": "FP", "FALSE POSITIVE": "FP",
}
# Punctuation that marks a separator as a deliberate verdict delimiter rather
# than the ordinary space after a sentence (see _is_later_verdict).
_SEP_PUNCT_RE = re.compile(r"[:.—-]")
# A "Check N" reference inside the comment, if present.
_CHECK_RE = re.compile(r"\bcheck\s*#?\s*(\d+)\b", re.IGNORECASE)
# The date in a "[qa-agent] YYYY-MM-DD ..." issue title.
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# Bot accounts can NEVER certify a finding (self-certification defeats the Tier-2
# gate).  This is the default guard when no explicit operator allowlist is given —
# robust to not knowing the operator's exact GH login on a small private repo.
_BOT_RE = re.compile(r"\[bot\]$|^github-actions", re.IGNORECASE)
# A recorded ledger DATA row starts `| YYYY-MM-DD |` — the header, separator,
# and the empty-ledger placeholder row all fail this, so they parse to nothing.
_LEDGER_ROW_RE = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|")


def _is_eligible(author: str, operator_logins: "Optional[set]") -> bool:
    if operator_logins is not None:
        return author in operator_logins      # explicit strict allowlist
    return bool(author) and not _BOT_RE.search(author)   # default: humans only


def _issue_date(issue: dict) -> str:
    m = _DATE_RE.search(issue.get("title", "") or "")
    if m:
        return m.group(1)
    return (issue.get("createdAt") or issue.get("created_at") or "")[:10]


def _comment_author(c: dict) -> str:
    a = c.get("author")
    if isinstance(a, dict):
        return a.get("login") or ""
    return c.get("author_login") or (a or "")


def _is_later_verdict(text: str, m: "re.Match") -> bool:
    """Whether a NON-FIRST `TP`/`FP` occurrence is really a second verdict rather
    than prose.  Two guards, because the split must not manufacture rows:

    * The separator after the token must be NON-EMPTY.  An empty one is exactly
      the "TP/FP" prose form ("/" sits outside the separator class), which the
      operator writes when talking ABOUT the pair ("TP/FP verdicts are owed").
    * A verdict found mid-line — i.e. after a sentence terminator rather than at
      the start of one — must additionally carry PUNCTUATION in that separator
      ("TP — Check 2", "TP: Check 2").  Otherwise a sentence ending in an
      abbreviation swallows the next word: "…shipped in Aug. TP tallies are
      unaffected." would otherwise propose a phantom second TP.  A verdict
      opening its own line is trusted without punctuation, since that is the
      shape a bare "FP flake" line takes.
    """
    separator = m.group(2)
    if not separator:
        return False
    # Measured from the TOKEN, not from the match start: the two scan branches
    # overlap at a line break after a sentence ("real.\nFP flake"), where the
    # sentence branch starts earlier and therefore wins the alternation — so
    # m.start() lands on the newline and would misread a line-initial verdict as
    # mid-line.  Walking back from the token over indentation is branch-agnostic.
    before = text[:m.start(1)].rstrip(" \t")
    at_line_start = not before or before.endswith("\n")
    return at_line_start or bool(_SEP_PUNCT_RE.search(separator))


def _check_number(text: str, start: int, end: int) -> Optional[str]:
    """The first `Check N` inside a half-open window, or None.  Windowing is what
    lets a two-verdict comment attribute Check 15 to the first and Check 2 to the
    second — the pre-fix code searched the whole body once and gave both the
    first number it found."""
    m = _CHECK_RE.search(text[start:end])
    return m.group(1) if m else None


def _shape1_occurrences(text: str) -> list[tuple[str, str, int, int]]:
    """`(verdict, raw_reason, check_window_start, check_window_end)` per verdict
    for a body that OPENS with TP/FP; empty for any body that doesn't.

    That opening requirement is the anchor guard, and it is load-bearing rather
    than decorative: a 2026-08-13 operator batch-close comment on a daily issue
    reads "…disposition comments where present. TP/FP calibration verdicts are
    still owed…", whose "TP" sits at a sentence start with a word boundary.
    Scanning every comment for sentence-initial verdicts would mint a TP row
    out of that sentence; gating the scan on the body's FIRST token keeps it at
    zero rows, exactly as before this change.
    """
    matches: list[re.Match] = []
    for m in _VERDICT_SCAN_RE.finditer(text):
        if not matches:
            if m.start() != 0:
                break          # body does not open with a verdict → not shape 1
            matches.append(m)
        elif _is_later_verdict(text, m):
            matches.append(m)
    out: list[tuple[str, str, int, int]] = []
    for i, m in enumerate(matches):
        # Each reason is bounded by the NEXT verdict, not by end-of-body — the
        # DOTALL behavior that let verdict 1 swallow verdict 2.
        nxt = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # The FIRST verdict's check window opens at the body start, not at the
        # token: shape 2's 2026-07-22 comment named the check BEFORE the
        # verdict, and for a single-verdict comment this window is the whole
        # body — i.e. exactly what the pre-fix code searched.
        out.append((m.group(1).upper(), text[m.end():nxt],
                    0 if i == 0 else m.start(), nxt))
    return out


def _shape2_occurrences(body: str) -> list[tuple[str, str, int, int]]:
    """Same tuple, for EVERY `verdict: <token>` marker (not just the first)."""
    matches = list(_VERDICT_LINE_RE.finditer(body))
    out: list[tuple[str, str, int, int]] = []
    for i, m in enumerate(matches):
        nxt = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        token = " ".join(m.group(1).upper().split())
        # Shape 2's reason is the rest of THAT line only — the natural
        # "Finding (1-line)" for a verdict embedded in a longer comment.  Its
        # check window opens at the END of the previous marker's line so a check
        # named ahead of the marker (the 2026-07-22 shape) still attaches to it.
        out.append((_VERDICT_TOKENS[token], m.group(2),
                    0 if i == 0 else matches[i - 1].end(), nxt))
    return out


def _extract_verdicts(body: str) -> list[tuple[str, str, Optional[str]]]:
    """Every `(verdict, raw_reason, check_number|None)` a comment carries, in body
    order — a comment may hold more than one.  Shape 1 (body starts with TP/FP)
    still wins over shape 2 (`verdict: <token>` lines) when both would match.

    A comment holding exactly ONE verdict parses byte-identically to the
    pre-2026-08-18 single-verdict code: its reason still runs to end-of-body and
    its check is still the first `Check N` anywhere in the body.  That equivalence
    is the main regression risk in this change, so pin it directly with a
    byte-identical-render test.
    """
    text = body.strip()
    occurrences = _shape1_occurrences(text)
    if occurrences:
        return [(v, r, _check_number(text, a, b)) for v, r, a, b in occurrences]
    return [(v, r, _check_number(body, a, b))
            for v, r, a, b in _shape2_occurrences(body)]


def parse_ledger_pairs(ledger_text: str) -> "set[tuple[int, str]]":
    """The `(QA Issue number, Check)` pairs already recorded in the ledger
    table, so the retro never re-proposes a hand-recorded verdict (pasting a
    re-proposed row would double-count one finding toward the ≥3-TP Tier-2
    gate).  Keyed on the PAIR, not the issue: one daily QA issue can
    legitimately yield rows for two different checks."""
    pairs: set[tuple[int, str]] = set()
    for line in ledger_text.splitlines():
        if not _LEDGER_ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] is the empty string before the leading "|"; the ledger's
        # columns are Date=1, QA Issue=2, Check=3 (mirrors the header pin).
        if len(cells) < 4:
            continue
        m_issue = re.search(r"#(\d+)", cells[2])
        if not m_issue:
            continue
        m_check = _CHECK_RE.search(cells[3])
        pairs.add((
            int(m_issue.group(1)),
            f"Check {m_check.group(1)}" if m_check else "—",
        ))
    return pairs


def build_proposed_rows(
    issues: list[dict],
    operator_logins: Optional[set] = None,
    recorded: "Optional[set[tuple[int, str]]]" = None,
) -> list[dict]:
    """For each operator TP/FP comment on a `[qa-agent]` issue, build a proposed
    ledger row.  `operator_logins` (if given) is a strict allowlist of who counts
    as the operator; when omitted, any non-bot human counts and `[bot]` accounts
    are excluded — either way the agent can never self-certify its own findings.
    `recorded` is the ledger's existing `(issue, check)` pairs (see
    `parse_ledger_pairs`); a matching row is tagged `already_recorded` so the
    renderer can list it as skipped instead of proposing it again."""
    recorded = recorded or set()
    rows: list[dict] = []
    for issue in issues:
        date = _issue_date(issue)
        num = issue.get("number")
        for c in issue.get("comments", []) or []:
            author = _comment_author(c)
            if not _is_eligible(author, operator_logins):
                continue
            # ONE ROW PER VERDICT — a single comment can carry several
            # (2026-08-18: one comment held two TPs on one line, and the old
            # single-append loop reported one).
            for verdict, raw_reason, check_num in _extract_verdicts(c.get("body") or ""):
                reason = " ".join(raw_reason.split())[:160] or "(no reason given)"
                check = f"Check {check_num}" if check_num else "—"
                rows.append({
                    "date": date, "issue": num, "check": check,
                    "verdict": verdict, "reason": reason, "author": author,
                    # Dedupe still keys on the (issue, check) PAIR, which is what
                    # makes two checks from ONE comment independently dedupable.
                    "already_recorded": (num, check) in recorded,
                })
    return rows


def render_markdown(rows: list[dict]) -> str:
    if not rows:
        return (
            "No new operator `TP`/`FP` verdicts found on recent `[qa-agent]` "
            "issues.\n\nDrop a `TP` or `FP` comment (with a one-line reason) on a "
            "daily QA issue and the next retro will propose its ledger row."
        )
    new = [r for r in rows if not r.get("already_recorded")]
    dup = [r for r in rows if r.get("already_recorded")]
    lines: list[str] = []
    if new:
        # The tally counts only NEW rows — an already-recorded verdict must not
        # count toward the gate a second time (that's the whole point of the
        # dedupe).
        tp = sum(1 for r in new if r["verdict"] == "TP")
        fp = sum(1 for r in new if r["verdict"] == "FP")
        lines += [
            "Proposed `docs/calibration_ledger.md` rows from this week's "
            "`[qa-agent]` verdicts — **operator sign-off required**: paste the "
            "approved rows into the ledger (they render in the ledger's own "
            "column order, so an approved row pastes verbatim). The bookkeeper "
            "never edits the ledger or self-certifies (the operator's comment is "
            "the authority).",
            "",
            f"**This batch:** {tp} TP · {fp} FP — counts toward the Tier-2 gate "
            f"(≥3 TP + 14 clean days; see `docs/promotion_criteria.md`).",
            "",
            LEDGER_HEADER,
            LEDGER_SEPARATOR,
        ]
        for r in new:
            finding = r["reason"].replace("|", r"\|")   # a stray | would split the cell
            lines.append(
                f"| {r['date']} | #{r['issue']} | {r['check']} | {finding} | "
                f"{r['verdict']} | by @{r['author']} |"
            )
    else:
        lines.append(
            "No new operator `TP`/`FP` verdicts to propose — every verdict "
            "found in this window is already recorded in the ledger (listed "
            "below)."
        )
    if dup:
        lines += [
            "",
            "**Already in the ledger — do not paste** (deduped on the "
            "`(QA Issue, Check)` pairs in `docs/calibration_ledger.md`):",
            "",
        ]
        for r in dup:
            lines.append(
                f"- #{r['issue']} · {r['check']} · {r['verdict']} "
                f"(verdict by @{r['author']})"
            )
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - thin I/O wrapper
    import json
    import os
    import sys
    from pathlib import Path

    raw = sys.stdin.read()
    issues = json.loads(raw) if raw.strip() else []
    ops = os.getenv("QA_LEDGER_OPERATORS", "")
    operator_logins = {x.strip() for x in ops.split(",") if x.strip()} or None
    # The ledger is a repo-local file (the workflow runs this script from the
    # checkout root); reading it keeps the module network-free.  Missing/moved
    # file → empty set, i.e. fail-open to no-dedupe rather than a dead retro:
    # every proposal still goes through operator sign-off, so the worst case is
    # a duplicate PROPOSAL, never a duplicate ledger row by itself.
    ledger = Path(os.getenv("QA_LEDGER_PATH") or "docs/calibration_ledger.md")
    recorded = (
        parse_ledger_pairs(ledger.read_text(encoding="utf-8"))
        if ledger.is_file() else set()
    )
    print(render_markdown(build_proposed_rows(issues, operator_logins, recorded=recorded)))


if __name__ == "__main__":  # pragma: no cover
    main()
