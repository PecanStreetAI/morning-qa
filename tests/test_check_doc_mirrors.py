"""Doc-mirror lockstep pins: check specs ↔ check catalog ↔ SKILL.md ↔ scripts.

These hand-maintained mirrors demonstrably drift — the production system this
framework was extracted from found its catalog tables and its check files
disagreeing more than once (a 2026-07-18 calibration pass caught two at once) —
so the enumerable invariants are pinned here and fail at PR time instead of
surfacing weeks later as "the docs say two different things" findings.

Two conventions an adopter should copy when extending this suite:

* **Flatten before matching.**  The prose hard-wraps at ~72 cols, so every
  multi-word pin matches whitespace-collapsed text — a pure re-wrap that
  changes nothing must never red a pin (and a pin must never dictate where a
  line breaks).
* **Subset relations are the contract where the surfaces deliberately differ.**
  The catalog is the rubric surface and carries the fuller field contract; a
  check spec quotes the subset its steps actually use.  Byte-equality pins
  belong only where the docs promise byte-equality.
"""

import re

from probes import (
    CATALOG,
    PRECOMPUTE_DOC,
    PRECOMPUTE_SCRIPT,
    SKILL,
    catalog_section,
    flat,
    load_script_module,
    severity_rows,
    spec_path,
)

qa = load_script_module(PRECOMPUTE_SCRIPT, "qa_precompute")

_SPEC_09 = spec_path(9)
_SPEC_07 = spec_path(7)

# The advisory-id shapes an allowlist row can be keyed by.  GHSA ids matter:
# some advisories ship no CVE at all, and a mirror pin blind to them lets a
# GHSA-keyed row escape the very drift guard this module exists to be.
_ADVISORY_RE = re.compile(
    r"CVE-\d{4}-\d{4,7}|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}"
)


# ── Check-count lockstep across every surface ────────────────────────────────
def test_check_roster_consistent_across_catalog_skill_and_script():
    """Catalog `## Check N` sections == SKILL.md run-list numbers == the
    report-template's table rows == the precompute script's check set.  The
    production instance shipped a self-contradictory roster header once
    (2026-07-21 review catch) — derive the numbers, never trust prose."""
    catalog_checks = {
        int(n) for n in re.findall(r"^## Check (\d+) —", CATALOG.read_text(encoding="utf-8"), re.M)
    }
    skill = SKILL.read_text(encoding="utf-8")
    # The run list lives in § "Run the checks" — scoped, because SKILL.md has
    # other column-0 numbered-bold lists (the Mongo-fallback steps, the
    # quarterly-review questions) whose numbers are not check ids.
    assert "## Run the checks" in skill, "SKILL.md § Run the checks renamed?"
    run_section = skill.split("## Run the checks", 1)[1].split("\n## ", 1)[0]
    run_list = {int(n) for n in re.findall(r"^(\d+)\. \*\*", run_section, re.M)}
    template_rows = {int(n) for n in re.findall(r"^\| (\d+) \| ", skill, re.M)}

    assert catalog_checks == run_list == template_rows == set(qa.PRECOMPUTED_CHECKS), (
        f"check roster drifted: catalog={sorted(catalog_checks)} "
        f"skill-run-list={sorted(run_list)} report-template={sorted(template_rows)} "
        f"script={sorted(qa.PRECOMPUTED_CHECKS)}"
    )
    assert set(qa.CHECK_FNS) == set(qa.PRECOMPUTED_CHECKS), (
        "the script's check-function registry no longer covers exactly "
        "PRECOMPUTED_CHECKS"
    )
    # deploy_state is a run-wide section, not a check — it must stay outside
    # the registry so the report's used-N/P accounting does not silently shift.
    assert qa.deploy_state not in qa.CHECK_FNS.values()

    # {N} in the Status line counts the numbered checks (Check 0 is a gate and
    # is NOT counted) — derived, so adding a check forces the template to move.
    m = re.search(r"\*\*(\d+)\*\* in this template", flat(skill))
    assert m, "SKILL.md no longer states the {N} count for this template"
    assert int(m.group(1)) == len(catalog_checks) - 1


def test_precompute_doc_mirrors_the_script_set_and_the_p_count():
    """docs/precompute.md restates the pre-computed set and the P count the
    report's observability line divides by; both are derived here so a set
    change fails until the doc moves with it."""
    doc = PRECOMPUTE_DOC.read_text(encoding="utf-8")
    m = re.search(r"the set is `\{([0-9, ]+)\}`", flat(doc))  # flat: the sentence hard-wraps
    assert m, "precompute.md no longer states the pre-computed set"
    assert {int(n) for n in m.group(1).split(",")} == set(qa.PRECOMPUTED_CHECKS)
    shipped = set(re.findall(r"(\d+) as shipped", flat(doc)))
    assert shipped == {str(len(qa.PRECOMPUTED_CHECKS))}, (
        f"precompute.md's 'P as shipped' restatements {sorted(shipped)} != "
        f"len(PRECOMPUTED_CHECKS) = {len(qa.PRECOMPUTED_CHECKS)}"
    )
    assert "Pre-computed inputs" in SKILL.read_text(encoding="utf-8"), (
        "SKILL.md is missing its § Pre-computed inputs section"
    )


def test_skill_and_precompute_doc_keep_saying_used_n_of_p():
    """The report's observability line — `pre-compute: used N/P, fell back M`
    — is specified in two places; an edit that rewords one leaves the agent
    and the doc describing different lines."""
    for name, text in (("SKILL.md", SKILL.read_text(encoding="utf-8")),
                       ("precompute.md", PRECOMPUTE_DOC.read_text(encoding="utf-8"))):
        assert "pre-compute: used N/P, fell back M" in flat(text), (
            f"{name} no longer specifies the `used N/P, fell back M` line"
        )


# ── Check 9: the zero-yield contract (spec ⊆ catalog) ────────────────────────
_YIELD_CONTRACT = {"zero_yield", "polled", "failed", "effective_failed",
                   "window_hours", "finished_at"}
_YIELD_SPEC_SUBSET = {"polled", "failed", "effective_failed", "finished_at"}


def _word(field, text):
    return re.search(rf"(?<![A-Za-z_]){field}(?![A-Za-z_])", text)


def test_check9_zero_yield_field_contract_spec_subset_of_catalog():
    """The catalog's Check-9 section carries the full OUTCOME-block contract;
    the spec's steps quote the subset they use.  Motivating incident
    (2026-08-26 in the production instance): a polling collector shipped and
    collected nothing for six days — ~1,800 green heartbeats over ~800
    consecutive vendor auth failures — because a heartbeat marks success when
    the job returns normally, and a pass that fails every item still returns
    normally.  The yield fields are the reader that closes that blind spot, so
    the two surfaces naming them must agree."""
    catalog9 = flat(catalog_section(9))
    m = re.search(r"OUTCOME block — (.*?) — summing", catalog9)
    assert m, "catalog Check-9 no longer enumerates the OUTCOME-block fields"
    assert set(re.findall(r"`(\w+)`", m.group(1))) == _YIELD_CONTRACT, (
        "catalog OUTCOME-block field list drifted from the documented contract"
    )
    spec9 = _SPEC_09.read_text(encoding="utf-8")
    for field in _YIELD_SPEC_SUBSET:
        assert _word(field, spec9), f"spec 09 no longer names yield field {field}"
        assert _word(field, catalog9), f"catalog Check-9 no longer names yield field {field}"
    # The spec may name benign-skip categories (deliberately app-specific
    # examples), but any other meta.* counter it reads must be in the
    # catalog's contract — a spec-only counter is a reader the rubric surface
    # doesn't know about.
    for name in set(re.findall(r"meta\.([a-z_]+)", spec9)):
        assert name.startswith("skipped_") or name in _YIELD_CONTRACT, (
            f"spec 09 reads meta.{name}, which the catalog's OUTCOME contract "
            "does not carry"
        )


def test_check9_zero_yield_row_is_a_warning_on_both_surfaces():
    """Both surfaces carry the zero-yield severity row, keyed on the OUTCOME
    wording (`polled == 0` AND `effective_failed > 0` — the passes ran and
    produced only failures) and graded 🟡.  Pinned on the VALUE, not just
    mirrored: a mirror-only pin is blind to an edit applied to both sides, and
    a both-surface downgrade to a clean glyph would re-open the exact six-day
    blind spot the row exists to close."""
    for name, text in (("catalog", catalog_section(9)),
                       (_SPEC_09.name, _SPEC_09.read_text(encoding="utf-8"))):
        rows = [r for r in severity_rows(text) if "effective_failed > 0" in r]
        assert rows, f"{name}: the zero-yield severity row is gone"
        for r in rows:
            assert "🟡" in r and "✅" not in r and "🟢" not in r, (
                f"{name}: a zero-yield row is no longer a Warning: {r}"
            )
        assert any(
            "produced only failures" in r.lower() and "net of benign skips" in r
            for r in rows
        ), f"{name}: the zero-yield row lost its outcome wording"


def test_check9_escalation_is_two_mornings_gated_on_yesterdays_issue_body():
    """The 🟡→🔴 escalation is a PERSISTENCE rule, and it must stay gateable
    without a second database read: yesterday's zero-yield value rides the
    verbatim daily-issue body.  Pin the window and the evidence source in the
    operative clause on BOTH surfaces — a clause rewritten to "escalate
    immediately" or to demand a fresh database probe would each break a
    different half of the design."""
    for name, text in (("catalog", catalog_section(9)),
                       (_SPEC_09.name, _SPEC_09.read_text(encoding="utf-8"))):
        m = re.search(r"Escalation(?:\s*\(dwell\))?:\**(.*?)treat as Critical", flat(text))
        assert m, f"{name}: the operative escalation clause is gone"
        clause = m.group(1)
        assert "two consecutive mornings" in clause, (
            f"{name}: the escalation window is gone from the operative clause"
        )
        assert re.search(r"yesterday'?s QA [Ii]ssue", clause), (
            f"{name}: the escalation no longer names yesterday's issue body "
            "as the evidence source"
        )


def test_check9_measured_zero_and_null_semantics_on_both_surfaces():
    """A measured 0 is a value (an idle window is healthy) and a null is a
    third state (nothing readable — not an alarm, and NOT the same as 0).
    Losing either sentence re-opens a falsy-zero class: coercing "could not
    read" to 0 fabricates health exactly as readily as reading 0 as broken."""
    for name, text in (("catalog", flat(catalog_section(9))),
                       (_SPEC_09.name, flat(_SPEC_09.read_text(encoding="utf-8")))):
        assert "A measured 0 is a value" in text, f"{name}: measured-zero sentence gone"
        assert "not an alarm" in text, f"{name}: null-is-not-an-alarm sentence gone"
        assert "NOT the same as `0`" in text or "NOT the same as 0" in text, (
            f"{name}: the null-vs-zero distinction is gone"
        )


def test_check9_catalog_signals_lead_with_the_blind_row():
    """The catalog's own format rule: severity tables read top-down,
    first-match-wins, and the "check is blind" row sits FIRST so an
    unavailable input can never fall through to a clean row.  Its shipped
    Check-9 table must practice what the format section preaches."""
    section = catalog_section(9)
    assert "**Signals:**" in section
    rows = severity_rows(section[section.index("**Signals:**"):])
    assert "unreachable" in rows[0] and "🟡" in rows[0], (
        f"catalog Check-9 signals no longer lead with the blind row: {rows[0]}"
    )
    assert "never a silent clean" in rows[0]


def test_job_heartbeats_collection_named_in_lockstep():
    """The gate-scope table in SKILL.md, the spec's Mongo query template, and
    the catalog must all name the same heartbeat collection — a rename in the
    app that updates one surface leaves the agent cross-checking a collection
    that no longer exists (which renders as "no rows", the dangerous shape)."""
    assert re.search(r"^\| `job_heartbeats` \| Check 9", SKILL.read_text(encoding="utf-8"), re.M), (
        "SKILL.md gate-scope table lost its job_heartbeats row"
    )
    assert "collection: job_heartbeats" in _SPEC_09.read_text(encoding="utf-8")
    assert "job_heartbeats" in catalog_section(9)


# ── Check 7: CI-toolchain + allowlist mirrors ────────────────────────────────
def test_check7_ci_tool_packages_and_thresholds_agree():
    """The script's CI_TOOL_PACKAGES is the watched set; the spec names the
    same packages, and both doc surfaces quote the same 30-day staleness
    threshold and the same Warning-not-Critical ceiling for CI-tool
    advisories (dev/CI-only tooling never ships to a user)."""
    spec = _SPEC_07.read_text(encoding="utf-8")
    for pkg in qa.CI_TOOL_PACKAGES:
        assert pkg in spec, f"spec 07 no longer names watched CI tool {pkg}"
    catalog7 = flat(catalog_section(7))
    spec_flat = flat(spec)
    for name, text in (("catalog", catalog7), (_SPEC_07.name, spec_flat)):
        assert re.search(r">\s?30 days", text), f"{name}: the 30-day threshold is gone"
        assert "warning, not critical" in text.lower(), (
            f"{name}: the CI-tool advisories-are-Warning rule is gone"
        )


def test_check7_allowlist_rows_parse_and_catalog_ids_are_backed():
    """The Accepted-CVE allowlist lives in the spec (the catalog defers to it
    by design); every row must carry a non-empty Re-verify cell — the only
    live bound on a suppression whose version condition is vacuously true —
    and a Remove-when.  Any advisory id the catalog DOES name must be backed
    by a spec row (a catalog id with no spec row is drift; the reverse is
    fine — the catalog summarizes)."""
    spec = _SPEC_07.read_text(encoding="utf-8")
    header = next(
        (l for l in spec.splitlines() if l.startswith("| CVE / GHSA |")), None
    )
    assert header, "spec 07 allowlist table header changed"
    for col in ("Unfixed range", "Why accepted", "Re-verify each run", "Remove-when"):
        assert col in header, f"allowlist header lost the {col} column"
    rows = [l for l in spec.splitlines() if l.startswith(("| CVE-", "| GHSA-"))]
    assert rows, "spec 07 allowlist has no parseable data row (shape drifted?)"
    spec_ids = set()
    for row in rows:
        cells = [c.strip() for c in row.split("|")]
        assert len(cells) >= 8, f"allowlist row does not have 6 cells: {row[:80]}"
        spec_ids |= set(_ADVISORY_RE.findall(cells[1]))
        assert "grep" in cells[5], (
            "an allowlist row has no concrete re-verify command — the "
            "rationale would be taken on faith"
        )
        assert cells[6], "an allowlist row has no Remove-when condition"
    catalog_ids = set(_ADVISORY_RE.findall(catalog_section(7)))
    assert catalog_ids <= spec_ids, (
        f"catalog names advisory ids with no backing spec row: "
        f"{sorted(catalog_ids - spec_ids)}"
    )


# ── Precompute doc ↔ script: the unavailable-≠-clean table ──────────────────
def test_precompute_doc_availability_flags_exist_in_the_script():
    """docs/precompute.md's invariant table names the per-probe availability
    flags; each must exist in the script, and the script must carry the
    doc-promised failure renders.  This is the doc-pinned-to-code direction:
    a doc describing a flag the code dropped would teach an adopter a guard
    that is not there."""
    doc_flags = set(re.findall(r"`(\w+_ok)`", PRECOMPUTE_DOC.read_text(encoding="utf-8")))
    assert {"pip_audit_ok", "npm_audit_ok", "pip_outdated_ok", "npm_outdated_ok"} <= doc_flags
    src = PRECOMPUTE_SCRIPT.read_text(encoding="utf-8")
    for flag in doc_flags:
        assert flag in src, f"precompute.md documents {flag} but the script lost it"
    for literal in ("pip-audit unavailable", "npm audit UNAVAILABLE",
                    "non-JSON (agent: Warning per Edge cases)",
                    "deploy state NOT determined"):
        assert literal in src, (
            f"the script no longer renders {literal!r} — the doc's failure-"
            "render table is stale"
        )


def test_deploy_state_rule_mirrored_in_skill_and_precompute_doc():
    """The deploy-state probe is only half the fix; the agent needs the RULE.
    A 2026-08-15 production report relabelled a commit's merge time as a
    deploy time (the real deploy ran six hours after the report) — a
    fabricated fact renders exactly like a measured one, so both docs must
    keep the prohibition, the honest-gap wording, and the ancestry probe."""
    skill = flat(SKILL.read_text(encoding="utf-8"))
    assert "a merge time is NOT a deploy time" in skill
    assert "deploy state not determined" in skill
    assert "merge-base --is-ancestor" in skill
    doc = flat(PRECOMPUTE_DOC.read_text(encoding="utf-8"))
    assert "a merge time is never a deploy time" in doc
    assert "merge-base --is-ancestor" in doc
    assert "`undeployed: 0`" in doc, (
        "precompute.md no longer forbids rendering an unknown deploy state "
        "as undeployed: 0"
    )


def test_each_precomputed_spec_carries_banner_and_review_stamp():
    """Every pre-computed check spec opens with its precompute banner (its
    probe steps ARE the fallback path) and a `Last reviewed:` stamp (the
    skill's quarterly-review cadence keys off it)."""
    for n in qa.PRECOMPUTED_CHECKS:
        text = spec_path(n).read_text(encoding="utf-8")
        assert text.startswith(f"# Check {n} —"), f"spec {n:02d} title drifted"
        assert "_Last reviewed: 20" in text[:300], f"spec {n:02d} lost its review stamp"
        assert "Pre-computed:" in text, f"spec {n:02d} lost its precompute banner"
        assert "/tmp/qa-precompute/bundle.md" in text, (
            f"spec {n:02d} banner no longer points at the bundle path"
        )
