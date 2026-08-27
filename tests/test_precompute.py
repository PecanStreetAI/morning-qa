"""Behavioral pins for template/.github/scripts/qa_precompute.py.

The precompute step moves mechanical data-gathering out of the agent's model
loop (turn count — not model choice — drives cost and the timeout risk; see
docs/precompute.md).  This suite pins the three things that make that safe:

1. **The pure extract_*/parse_* helpers turn raw probe payloads into FACTS,
   never verdicts** — driven with fixtures so their edge cases are reachable
   in CI, not just on a live run.
2. **Fail-soft is load-bearing.**  The 30-minute agent cap was deliberately
   kept as the "something is wrong" signal, so the script must ALWAYS exit 0
   and a broken probe must degrade to the agent self-gathering — never eat
   the budget, never crash the bundle.
3. **Unavailable ≠ clean.**  A probe that returns nothing and a probe that
   returns nothing BAD produce the same empty list, and the agent is told not
   to re-run an OK block — so every failure path must render as UNAVAILABLE /
   SKIPPED / NOT determined, never as a zero that reads like a clean bill.
   Each pin here that looks paranoid is a dated production incident: an npm
   audit whose discarded exit code printed "none" through a 75-second timeout
   (2026-07-28), a deploy probe whose absence got backfilled with a merge
   time (2026-08-15), a same-day re-run that read its own report as
   "yesterday" and called an 8x-moved metric unchanged (2026-07-27).
"""

import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from probes import PRECOMPUTE_SCRIPT, load_script_module

qa = load_script_module(PRECOMPUTE_SCRIPT, "qa_precompute")

_LIVE = "a" * 40
_HEAD = "b" * 40


# ── The pre-computed set ─────────────────────────────────────────────────────
def test_precomputed_check_set_is_pinned():
    """The template ships checks 0/7/9 and precomputes all three.  Changing
    the set is a documented 6-step procedure (docs/precompute.md § Adding /
    removing) — this pin plus the doc mirrors make steps 1-3 fail until they
    agree."""
    assert qa.PRECOMPUTED_CHECKS == (0, 7, 9)
    assert set(qa.CHECK_FNS) == set(qa.PRECOMPUTED_CHECKS)


# ── Pure extractors (facts, never verdicts) ──────────────────────────────────
def test_check7_extractors_filter_to_majors_and_high_critical_only():
    pip_rows = [
        {"name": "fastapi", "version": "0.100.0", "latest_version": "0.115.0"},  # 0->0
        {"name": "cryptography", "version": "44.0.0", "latest_version": "49.0.0"},  # major
        {"name": "foo", "version": "1.2.3", "latest_version": "1.9.0"},  # minor
    ]
    assert {m["name"] for m in qa.extract_pip_outdated(pip_rows)} == {"cryptography"}

    npm_payload = {
        "react": {"current": "18.2.0", "latest": "19.0.0"},  # major
        "vite": {"current": "5.4.0", "latest": "5.9.0"},  # minor
    }
    assert {m["name"] for m in qa.extract_npm_outdated(npm_payload)} == {"react"}

    # pip-audit: both the {"dependencies": [...]} shape and the older bare list.
    wrapped = {"dependencies": [
        {"name": "ecdsa", "version": "0.19.2",
         "vulns": [{"id": "GHSA-wj6h-64fc-37mp", "fix_versions": []}]},
        {"name": "safe", "version": "1.0.0", "vulns": []},
    ]}
    assert [v["package"] for v in qa.extract_pip_audit(wrapped)] == ["ecdsa"]
    bare = [{"name": "x", "version": "1.0", "vulns": [{"id": "CVE-1", "fix_versions": ["1.1"]}]}]
    assert [v["id"] for v in qa.extract_pip_audit(bare)] == ["CVE-1"]

    # npm audit: a moderate advisory must NOT surface as HIGH/CRITICAL.
    audit = {"vulnerabilities": {
        "postcss": {"severity": "high", "range": "<8.5.19",
                    "via": [{"source": 123, "title": "PostCSS"}], "fixAvailable": True},
        "lodash": {"severity": "moderate", "via": [{"title": "proto"}]},
    }}
    assert {h["package"] for h in qa.extract_npm_audit(audit)} == {"postcss"}


def test_extract_cron_health_is_tristate_and_small_parsers_never_raise():
    """`ok` is a tri-state: True / False / None-for-unreadable.  A payload the
    probe could not parse must render as unavailable, never as fresh — so no
    field gets a healthy default."""
    ex = qa.extract_cron_health({"ok": False, "stuck_count": 1,
                                 "stuck": [{"job_id": "x"}], "expected_jobs": ["a", "b"]})
    assert ex["ok"] is False and ex["stuck_count"] == 1
    assert ex["stuck"] == [{"job_id": "x"}] and ex["expected_jobs"] == ["a", "b"]
    assert qa.extract_cron_health("not a dict")["ok"] is None

    df = ("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
          "/dev/root 100 73 27 73% /\n"
          "tmpfs 10 1 9 10% /tmp\n")
    assert qa.parse_root_used_pct(df) == 73
    assert qa.parse_root_used_pct("garbage\nno mountpoint here") is None

    assert qa._parse_iso("2026-07-22T19:55:32.144Z") is not None
    assert qa._parse_iso("not-a-date") is None and qa._parse_iso(1234) is None
    assert qa._days_between("2026-07-22T00:00:00Z", "2026-07-24T00:00:00Z") == 2
    assert qa._days_between("2026-07-24T00:00:00Z", "2026-07-22T00:00:00Z") == -2  # 'ahead'
    assert qa._days_between(None, "2026-07-24T00:00:00Z") is None
    assert qa._ci_tool_slug("@anthropic-ai/claude-code") == "anthropic_ai_claude_code"


# ── Deploy receipt: the build-id extractor asserts the SHAPE ─────────────────
def test_extract_build_id_accepts_real_shapes_and_ignores_prefixed_names():
    """The leading word boundary is load-bearing: without it a receipt that
    also stamped the PREVIOUS build (`PREV_BUILD_ID = …`) hands back the wrong
    SHA — a confident wrong answer, the exact failure class this probe exists
    to close (it would report a live change as undeployed, with no tell)."""
    assert qa.extract_build_id(f'const BUILD_ID = "{_LIVE}";') == _LIVE
    assert qa.extract_build_id(f"const BUILD_ID='{_LIVE}';") == _LIVE
    assert qa.extract_build_id(f'self.BUILD_ID   =   "{_LIVE}"') == _LIVE
    prev, live = "1" * 40, "2" * 40
    js = f'const PREV_BUILD_ID = "{prev}";\nconst BUILD_ID = "{live}";'
    assert qa.extract_build_id(js) == live
    assert qa.extract_build_id(f'const OLD_BUILD_ID = "{prev}";') is None


def test_extract_build_id_rejects_everything_that_is_not_a_full_sha():
    """A partial match is worse than no match: it renders as a confident SHA
    the ancestry check then answers wrongly.  Assert the shape — a truncated
    id, an error page served at 200, uppercase, non-hex, and a longer hex run
    all read as 'no deploy state', never as a plausible string."""
    for label, text in [
        ("empty", ""),
        ("None", None),
        ("error page at 200", "<!doctype html><title>502 Bad Gateway</title>"),
        ("short id", 'const BUILD_ID = "aff31c3";'),
        ("39 chars", 'const BUILD_ID = "' + "a" * 39 + '";'),
        ("41 chars", 'const BUILD_ID = "' + "a" * 41 + '";'),
        ("uppercase", 'const BUILD_ID = "' + "A" * 40 + '";'),
        ("non-hex", 'const BUILD_ID = "' + "z" * 40 + '";'),
        ("different key", f'const BUILD_VERSION = "{_LIVE}";'),
    ]:
        assert qa.extract_build_id(text) is None, f"accepted a non-SHA: {label}"


# ── CI-toolchain pin freshness (Check 7 step 6) ──────────────────────────────
def test_extract_npm_dist_freshness_counts_days_and_stable_releases():
    """days_behind = latest publish - pinned publish; releases_behind counts
    STABLE versions after the pin (prereleases + the created/modified meta
    keys excluded, time-ordered so non-contiguous numbering is fine).  A pin
    absent from the registry is flagged, never a crash; a malformed packument
    yields safe empties."""
    packument = {
        "dist-tags": {"latest": "2.1.220", "stable": "2.1.212", "next": "2.1.220"},
        "time": {
            "created": "2025-01-01T00:00:00.000Z",
            "modified": "2026-07-24T23:11:21.821Z",
            "2.1.212": "2026-07-18T00:00:00.000Z",
            "2.1.218": "2026-07-22T19:55:32.144Z",
            "2.1.219": "2026-07-23T10:00:00.000Z",
            "2.1.220": "2026-07-24T23:11:21.821Z",
            "2.1.221-alpha.1": "2026-07-25T00:00:00.000Z",  # prerelease — NOT counted
        },
    }
    fr = qa.extract_npm_dist_freshness("2.1.218", packument)
    assert fr["latest"] == "2.1.220" and fr["stable"] == "2.1.212"
    assert fr["pinned_found"] is True and fr["is_latest"] is False
    assert fr["days_behind"] == 2
    assert fr["releases_behind"] == 2
    cur = qa.extract_npm_dist_freshness("2.1.220", packument)
    assert cur["is_latest"] is True and cur["days_behind"] == 0 and cur["releases_behind"] == 0
    miss = qa.extract_npm_dist_freshness("9.9.9", packument)
    assert miss["pinned_found"] is False and miss["days_behind"] is None
    bad = qa.extract_npm_dist_freshness("1.0.0", "not a dict")
    assert bad["latest"] is None and bad["pinned_found"] is False


def test_npm_advisories_distinguish_clean_from_unavailable(monkeypatch, tmp_path):
    """Load-bearing: a failed advisory check must read as UNAVAILABLE (None →
    agent Warning), never as a false all-clear ({} → 0 advisories).  npm's
    bulk endpoint version-filters server-side, so a normalized [] genuinely
    means clean — the two must never collapse."""
    advs = qa.extract_npm_advisories([
        {"id": 1065, "severity": "high", "title": "Command Injection",
         "vulnerable_versions": "<4.17.21", "url": "https://example.com/adv"},
        "garbage",  # non-dict skipped, not a crash
    ])
    assert [a["id"] for a in advs] == [1065] and advs[0]["severity"] == "high"
    assert qa.extract_npm_advisories(None) == []

    monkeypatch.setattr(qa, "RAW_DIR", tmp_path)
    assert qa._npm_bulk_advisories({}) == {}  # nothing to query
    monkeypatch.setattr(qa, "run", lambda *a, **k: (0, '{"lodash":[{"id":1}]}', ""))
    assert qa._npm_bulk_advisories({"lodash": ["4.0.0"]}) == {"lodash": [{"id": 1}]}
    monkeypatch.setattr(qa, "run", lambda *a, **k: (0, "{}", ""))
    assert qa._npm_bulk_advisories({"lodash": ["4.99.0"]}) == {}  # clean, not None
    monkeypatch.setattr(qa, "run", lambda *a, **k: (7, "", "curl: (6) could not resolve host"))
    assert qa._npm_bulk_advisories({"lodash": ["4.0.0"]}) is None  # transport error
    monkeypatch.setattr(qa, "run", lambda *a, **k: (0, "<html>500</html>", ""))
    assert qa._npm_bulk_advisories({"lodash": ["4.0.0"]}) is None  # non-JSON body


def test_parse_ci_pins_ignores_comments_and_prose():
    """The comment-skip + `npm install` + `-g` anchor are the load-bearing
    defenses: this workflow's own comments mention the packages constantly
    (bump instructions, old floating forms), and a naive `pkg@version` search
    over the whole file could latch onto a commented example and report a
    wrong days-behind fact.  Synthetic confounders, same discipline as the
    workflow suite's pin-detector calibration."""
    claude, mongo = qa.CI_TOOL_PACKAGES
    wf = "\n".join([
        f"        # bump: npm view {claude} dist-tags   then swap the literal",
        f"        # old line was: npm install -g {claude}@9.9.9  # DO NOT MATCH",
        f"      - run: npm install -g {claude}@2.1.218  # published latest at pin time",
        "      - run: cd frontend && npm ci",
        f"        run: npm install -g {mongo}@1.14.0",
        f"        # {mongo}@latest was the old floating form",
    ])
    assert qa._parse_ci_pins_from_workflow(wf) == {claude: "2.1.218", mongo: "1.14.0"}
    # partial: one package pinned for real, the other only in prose
    partial = (f"        run: npm install -g {claude}@3.0.0\n"
               f"        # {mongo}@1.2.3 mentioned in a comment")
    assert qa._parse_ci_pins_from_workflow(partial) == {claude: "3.0.0"}
    # a decoy global install of an UNRELATED package must not leak in
    assert qa._parse_ci_pins_from_workflow("run: npm install -g some-other-tool@1.2.3") == {}
    assert qa._parse_ci_pins_from_workflow("") == {}


def test_pinned_versions_read_from_the_real_workflow(monkeypatch, tmp_path):
    """The check reads the pins it watches straight from the workflow's own
    install lines — the single source of truth — so the watcher can never
    drift from what actually installs.  Cross-guards the workflow suite from
    the other side: revert a pin to a bare name or dist-tag and this returns
    nothing for that package.  A missing workflow degrades to {} (the check's
    documented fallback line), never a raise."""
    pins = qa._pinned_ci_tool_versions()
    assert set(pins) == set(qa.CI_TOOL_PACKAGES)
    for pkg, ver in pins.items():
        assert re.fullmatch(qa._SEMVER, ver), f"{pkg} pin {ver!r} is not an exact semver"
    monkeypatch.setattr(qa, "REPO", tmp_path)  # no workflow here
    assert qa._pinned_ci_tool_versions() == {}


def test_format_ci_tool_line_never_renders_a_failed_check_as_clean():
    """The facts→line formatter is pure so its branches are reachable without
    a live run — including advisory-endpoint-unavailable (None, distinct from
    [] = clean) and a registry time-map gap, which must not print a bare
    'None' that reads like a value."""
    base = {"pinned": "1.0.0", "pinned_found": True, "latest": "1.2.0", "stable": "1.1.0",
            "days_behind": 30, "releases_behind": 4, "is_latest": False}
    line = qa.format_ci_tool_line("pkg", "1.0.0", base, [])
    assert "30 days / 4 stable releases behind" in line and "0 advisories" in line
    assert "CURRENT" in qa.format_ci_tool_line("pkg", "1.2.0", {**base, "is_latest": True}, [])
    adv = qa.format_ci_tool_line("pkg", "1.0.0", base,
                                 [{"severity": "high", "id": 42,
                                   "vulnerable_versions": "<1.1.0", "title": "Boom"}])
    assert "high 42" in adv and "Boom" in adv
    una = qa.format_ci_tool_line("pkg", "1.0.0", base, None)
    assert "UNAVAILABLE" in una and "0 advisories" not in una
    assert "NOT in registry" in qa.format_ci_tool_line(
        "pkg", "9.9.9", {**base, "pinned_found": False}, [])
    assert "lookup FAILED" in qa.format_ci_tool_line(
        "pkg", "1.0.0", {**base, "latest": None}, [])
    nodays = qa.format_ci_tool_line("pkg", "1.0.0", {**base, "days_behind": None}, [])
    assert "None days" not in nodays and "unknown days" in nodays


# ── Check 7: an audit that did not run must never render as clean ────────────
def _check7(monkeypatch, tmp_path, fake_run):
    monkeypatch.setattr(qa, "RAW_DIR", tmp_path)
    monkeypatch.setattr(qa, "_past_deadline", lambda: False)
    monkeypatch.setattr(qa, "_pinned_ci_tool_versions", lambda: {})  # skip the registry block
    monkeypatch.setattr(qa, "run", fake_run)
    return qa.check_7_deps()["body"]


def test_check7_npm_audit_failure_reads_unavailable_never_clean(monkeypatch, tmp_path):
    """The discarded-return-code trap (2026-07-28 production bug hunt): a
    75-second timeout, a registry 500, and an npm error DOCUMENT (npm prints
    its errors as valid JSON on stdout, so json.loads succeeding is not
    enough) all left the hit list empty and printed "none" — byte-identical to
    a genuinely clean audit, on a block the agent is told not to re-probe."""
    def runner(audit_stdout, audit_rc=0):
        def fake(cmd, **kw):
            joined = " ".join(cmd)
            if "pip-audit" in joined:
                return 0, "[]", ""
            if "--outdated" in joined:
                return 0, "[]" if "pip" in joined else "{}", ""
            if "audit" in joined:
                return audit_rc, audit_stdout, ""
            return 0, "", ""
        return fake

    clean = json.dumps({"auditReportVersion": 2, "vulnerabilities": {}, "metadata": {}})
    body = _check7(monkeypatch, tmp_path, runner(clean))
    assert "Frontend HIGH/CRIT (npm audit): none" in body  # the flag must not cry wolf

    body = _check7(monkeypatch, tmp_path, runner("", audit_rc=124))  # timeout
    assert "UNAVAILABLE" in body and "NOT a clean bill" in body
    assert "Frontend HIGH/CRIT (npm audit): none" not in body

    err_doc = json.dumps({"error": {"code": "ENOLOCK", "summary": "no lockfile"}})
    body = _check7(monkeypatch, tmp_path, runner(err_doc))  # valid JSON, still an error
    assert "UNAVAILABLE" in body
    assert "Frontend HIGH/CRIT (npm audit): none" not in body

    hit = json.dumps({"vulnerabilities": {"postcss": {
        "severity": "critical", "range": "<8.5.19", "via": [{"title": "x"}],
        "fixAvailable": True}}})
    assert "postcss critical" in _check7(monkeypatch, tmp_path, runner(hit))


def test_check7_outdated_failures_are_independent_and_never_zero(monkeypatch, tmp_path):
    """Same class, subtler trigger: `npm outdated --json` against an
    unreachable registry emits `{"error": {"code": "ECONNREFUSED", …}}` and
    exits 1 — but it ALSO exits 1 when packages simply are outdated, so the
    exit code cannot be the guard; the shape assertion is.  The two halves
    fail independently, so each one-sided case is pinned — a flag swap or an
    and→or in the caveat survives a both-fail-at-once test."""
    def runner(pip_stdout, npm_stdout):
        def fake(cmd, **kw):
            joined = " ".join(cmd)
            if "pip-audit" in joined:
                return 0, "[]", ""
            if "pip" in joined and "--outdated" in joined:
                return 0, pip_stdout, ""
            if "outdated" in joined:
                return 1, npm_stdout, ""  # npm outdated exits 1 routinely
            if "audit" in joined:
                return 0, '{"vulnerabilities":{}}', ""
            return 0, "", ""
        return fake

    err_doc = json.dumps({"error": {"code": "ECONNREFUSED", "summary": "FetchError"}})
    body = _check7(monkeypatch, tmp_path, runner("[]", err_doc))
    assert "Backend majors: 0" in body and "Frontend majors: unavailable" in body
    assert "Frontend majors: 0" not in body
    assert "partially-unavailable outdated list" in body

    body = _check7(monkeypatch, tmp_path, runner("not json", "{}"))
    assert "Backend majors: unavailable" in body and "Frontend majors: 0" in body

    body = _check7(monkeypatch, tmp_path, runner("[]", "{}"))
    assert "partially-unavailable outdated list" not in body


# ── Fail-soft plumbing ───────────────────────────────────────────────────────
def test_run_failsoft_codes_never_raise():
    rc, out, _ = qa.run(["this-command-does-not-exist-xyz"], timeout=5)
    assert rc == 127 and out == ""  # FileNotFoundError -> 127
    rc, _, _ = qa.run(["sleep", "5"], timeout=1)
    assert rc == 124  # TimeoutExpired -> 124


def test_safe_check_converts_an_exception_into_an_error_block(monkeypatch):
    """A bug in one check must never kill the bundle — the agent then runs
    that one check itself, exactly as it did before precompute existed."""
    def boom():
        raise ValueError("kaboom")
    monkeypatch.setitem(qa.CHECK_FNS, 7, boom)
    block = qa._safe_check(7)
    assert block["num"] == 7
    assert block["tag"].startswith("ERROR") and "kaboom" in block["tag"]


def test_redact_scrubs_every_secret_env_name_and_spares_short_strings(monkeypatch):
    """The _redact corpus must cover every name in _SECRET_ENV: a value set
    for any of them is scrubbed from bundle text (defense in depth over the
    fact that no code path constructs bundle text from a secret), while
    trivially short values are never redacted (that would mangle normal
    prose)."""
    import importlib.util

    values = {name: f"corpus-{i:02d}-abcdefabcdef" for i, name in enumerate(qa._SECRET_ENV)}
    for name, val in values.items():
        monkeypatch.setenv(name, val)
    spec = importlib.util.spec_from_file_location("qa_precompute_redact", PRECOMPUTE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod._SECRET_VALUES) == set(values.values())
    text = "start " + " mid ".join(values.values()) + " end"
    scrubbed = mod._redact(text)
    for val in values.values():
        assert val not in scrubbed
    assert scrubbed.count("***") == len(values)
    monkeypatch.setattr(qa, "_SECRET_VALUES", ["ab"])
    assert qa._redact("about") == "about"  # short strings spared


def test_every_credential_shaped_workflow_secret_is_in_the_redact_corpus():
    """The workflow's env block and the script's _SECRET_ENV must move
    together: any secrets.*-sourced env var whose NAME matches the skill's
    credential-name heuristic (KEY/SECRET/TOKEN/PASSWORD/URI/PAT/CREDENTIAL/
    DSN/CONNECTION_STRING) must be in the redact corpus, or a new secret ships
    un-scrubbed with no red anywhere."""
    from probes import all_run_steps, load_workflow

    cred_name = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|URI|PAT|CREDENTIAL|DSN|CONNECTION_STRING")
    for job_name, step in all_run_steps(load_workflow()):
        for key, val in (step.get("env") or {}).items():
            if "secrets." in str(val) and cred_name.search(key):
                assert key in qa._SECRET_ENV, (
                    f"{job_name}/{step.get('name')!r} exports secret env {key} "
                    "but qa_precompute._SECRET_ENV does not scrub it"
                )


# ── Check 9 rendering ────────────────────────────────────────────────────────
def _check9(monkeypatch, tmp_path, payload, status="200"):
    monkeypatch.setattr(qa, "RAW_DIR", tmp_path)
    monkeypatch.setattr(qa, "APP_BASE_URL", "https://app.example.test")
    monkeypatch.setenv("ADMIN_API_KEY", "k" * 12)
    monkeypatch.setattr(qa, "http_get", lambda *a, **k: {
        "rc": 0, "status": status, "content_type": "application/json", "err": "",
        "text": json.dumps(payload) if payload is not None else "",
        "json": payload, "body_path": "raw/cron_health.json", "hdr_text": ""})
    return qa.check_9_cron()


def test_check9_self_skips_without_its_url_or_its_key(monkeypatch):
    """Both gates degrade to SKIPPED with the reason named — so the template
    runs before an adopter has wired an app to it, and the agent knows to
    self-gather (or honestly report the check blind), never to read the
    absence as fresh."""
    monkeypatch.setattr(qa, "APP_BASE_URL", "")
    assert qa.check_9_cron()["tag"] == "SKIPPED: APP_BASE_URL unset"
    monkeypatch.setattr(qa, "APP_BASE_URL", "https://app.example.test")
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    assert qa.check_9_cron()["tag"] == "SKIPPED: ADMIN_API_KEY unset"


def test_check9_non_json_is_an_error_block_never_clean(monkeypatch, tmp_path):
    block = _check9(monkeypatch, tmp_path, None, status="500")
    assert block["tag"].startswith("ERROR")
    assert "Warning per Edge cases" in block["tag"]


def test_check9_unexplained_ok_false_never_prints_the_bare_clearance(monkeypatch, tmp_path):
    """ok=false with ZERO stuck rows means the endpoint flagged a condition
    the generic fields do not carry.  Twice in the production instance
    (2026-08-01, 2026-08-26) that combination was the FAST detector — a dead
    scheduler process, and a job running green while yielding only failures —
    and a renderer printing "all fresh" beside ok=false left the agent's only
    automated consumer dark on the morning it mattered."""
    block = _check9(monkeypatch, tmp_path, {
        "ok": False, "stuck_count": 0, "stuck": [],
        "checked_at": "2026-08-26T10:00:00+00:00", "expected_jobs": ["a", "b"]})
    assert "UNEXPLAINED" in block["body"]
    assert "All registered crons fresh." not in block["body"]


def test_check9_healthy_clearance_and_stuck_rows_render(monkeypatch, tmp_path):
    ok = _check9(monkeypatch, tmp_path, {
        "ok": True, "stuck_count": 0, "stuck": [],
        "checked_at": "2026-08-26T10:00:00+00:00", "expected_jobs": ["a", "b"]})
    assert "All registered crons fresh." in ok["body"]
    stuck = _check9(monkeypatch, tmp_path, {
        "ok": False, "stuck_count": 1,
        "stuck": [{"job_id": "nightly_ingest", "expected_cadence": "24h",
                   "last_success_at": "2026-08-24T09:12:00Z",
                   "hours_since": 49.1, "threshold_hours": 30}],
        "checked_at": "2026-08-26T10:00:00+00:00", "expected_jobs": ["a"]})
    assert "`nightly_ingest`" in stuck["body"] and "hours_since=49.1" in stuck["body"]
    assert "job_heartbeats" in stuck["body"]  # the cross-check reminder rides the rows


# ── Deploy state (the shared "what is actually LIVE" fact) ───────────────────
def _deploy(monkeypatch, tmp_path, *, status="200", text=None, hdr="",
            err="", head=_HEAD, git=None):
    monkeypatch.setattr(qa, "RAW_DIR", tmp_path)
    monkeypatch.setattr(qa, "APP_BASE_URL", "https://app.example.test")
    if text is None:
        text = f'const BUILD_ID = "{_LIVE}";'
    monkeypatch.setattr(qa, "http_get", lambda *a, **k: {
        "rc": 0, "status": status, "content_type": "application/javascript",
        "err": err, "text": text, "json": None, "body_path": "raw/deploy_receipt",
        "hdr_text": hdr})

    def _fake_git(args, timeout=None):
        if args[:1] == ["rev-parse"]:
            return (0, head + "\n", "")
        return (git or {}).get(args[0], (0, "", ""))

    monkeypatch.setattr(qa, "_git", _fake_git)
    return qa.deploy_state()


def test_deploy_state_reports_the_live_sha_and_labels_the_checkout(monkeypatch, tmp_path):
    """The checkout SHA must never stand unqualified: it used to be the only
    SHA in the agent's context, and it read as "the live one" — which is how
    a 2026-08-15 production report came to render a merge time as a deploy
    time."""
    out = _deploy(monkeypatch, tmp_path, git={
        "cat-file": (0, "", ""),
        "log": (0, "c0ffee1\tfeat(signals): guard\nbadcafe\tfix(x): y\n", "")})
    assert f"deployed_sha:  {_LIVE}" in out
    assert _HEAD in out and "NOT what is live" in out
    assert "undeployed:    2 commit(s)" in out
    assert "`c0ffee1` feat(signals): guard" in out


def test_deploy_state_zero_only_when_it_actually_measured_zero(monkeypatch, tmp_path):
    """The one branch allowed to say 0: the probe succeeded, the SHA resolved,
    and `git log` genuinely returned nothing ahead of it — plus the
    live-build-IS-this-checkout shortcut.  The deploy receipt's Last-Modified
    is surfaced as the only real deploy time, labelled as a file write time,
    never a commit time."""
    out = _deploy(monkeypatch, tmp_path,
                  hdr="HTTP/1.1 200 OK\r\nLast-Modified: Sat, 15 Aug 2026 16:33:36 GMT\r\n",
                  git={"cat-file": (0, "", ""), "log": (0, "", "")})
    assert "undeployed:    0" in out and "NOT determined" not in out
    assert "deployed_at:   Sat, 15 Aug 2026 16:33:36 GMT" in out
    assert "NOT a commit time" in out
    same = _deploy(monkeypatch, tmp_path, head=_LIVE)
    assert "undeployed:    0 — the live build IS this checkout." in same


def test_deploy_state_unknown_is_never_rendered_as_a_clean_bill(monkeypatch, tmp_path):
    """Unavailable ≠ clean at its sharpest: a probe that could not determine
    deploy state must not emit `undeployed: 0` (which reads as "the live
    build is current"), and the prohibition travels with the fact so the
    agent cannot backfill the gap with a commit timestamp."""
    for kwargs, why in [
        ({"status": "503", "err": "curl: (28) timeout"}, "HTTP 503"),
        ({"status": "", "text": ""}, "no response"),
        ({"text": "<!doctype html><title>502</title>"}, "no well-formed 40-char BUILD_ID"),
        ({"text": 'const BUILD_ID = "aff31c3";'}, "no well-formed 40-char BUILD_ID"),
    ]:
        out = _deploy(monkeypatch, tmp_path, **kwargs)
        assert "deployed_sha:  UNKNOWN" in out, why
        assert why in out
        assert "deploy state NOT determined" in out
        assert "undeployed:" not in out
        assert "is NOT a deploy time" in out


def test_deploy_state_unresolved_branches_and_never_raises(monkeypatch, tmp_path):
    """A live SHA outside a shallow checkout, and a FAILED `git log` (whose
    empty output is indistinguishable from "nothing is ahead" — the same trap
    as check 7's discarded audit exit code), both render UNRESOLVED.  And the
    probe never loses the bundle: any raise degrades to a FAILED line."""
    out = _deploy(monkeypatch, tmp_path, git={"cat-file": (1, "", "")})
    assert f"deployed_sha:  {_LIVE}" in out  # the SHA IS known...
    assert "ancestry:      UNRESOLVED" in out  # ...only the ancestry is not
    assert "deploy state NOT determined" in out and "undeployed:" not in out

    out = _deploy(monkeypatch, tmp_path, git={
        "cat-file": (0, "", ""), "log": (128, "", "fatal: bad object")})
    assert "undeployed:    UNRESOLVED" in out
    assert "undeployed:    0" not in out

    monkeypatch.setattr(qa, "RAW_DIR", tmp_path)
    monkeypatch.setattr(qa, "_git", lambda *a, **k: 1 / 0)
    out = qa.deploy_state()
    assert "FAILED (ZeroDivisionError)" in out
    assert "deploy state NOT determined" in out


# ── Yesterday's issue (self-feedback must never read today's own report) ─────
def _fake_issues(monkeypatch, issues):
    captured = []
    monkeypatch.setattr(qa, "GITHUB_REPO", "o/r")
    monkeypatch.setenv("GH_TOKEN", "x" * 8)

    def _stub(url, *a, **k):
        captured.append(url)
        return {"json": issues, "body_path": "raw/x.json"}

    monkeypatch.setattr(qa, "http_get", _stub)
    return captured


def test_fetch_yesterday_pins_the_query_params(monkeypatch):
    """Both params are load-bearing and invisible to the behavioral tests
    (which stub the response).  per_page reverted to 1 ⇒ a same-day re-run
    returns only today's issue, the prior-day filter empties it, and the real
    comparison is lost; direction flipped to asc ⇒ the OLDEST issue on the
    page is handed to the agent as "yesterday"."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    captured = _fake_issues(monkeypatch, [
        {"number": 40986, "title": "y", "body": "B", "created_at": f"{today}T00:00:00Z"},
    ])
    qa.fetch_yesterday()
    assert captured, "http_get was never called"
    url = captured[0]
    assert "per_page=5" in url
    assert "sort=created" in url and "direction=desc" in url
    assert "labels=qa-agent-daily" in url and "state=all" in url


def test_fetch_yesterday_skips_issues_created_today(monkeypatch):
    """The report step REUSES an already-open daily issue via `gh issue
    edit`, so a same-day re-run would otherwise fetch the very issue this run
    is about to overwrite and read its own morning report as "yesterday" —
    every self-feedback check then reports "unchanged" by construction.  That
    shipped in the production instance on 2026-07-27: a same-day re-run
    called a weekly metric flat while it had moved ~8x week-over-week."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    _fake_issues(monkeypatch, [
        {"number": 40991, "title": "today", "body": "TODAYS BODY",
         "created_at": f"{today}T13:14:24Z"},
        {"number": 40986, "title": "yesterday", "body": "PRIOR BODY",
         "created_at": f"{yday}T12:04:09Z"},
    ])
    out = qa.fetch_yesterday()
    assert "#40986" in out and "PRIOR BODY" in out
    assert "#40991" not in out and "TODAYS BODY" not in out


def test_fetch_yesterday_with_only_todays_issue_forbids_claiming_unchanged(monkeypatch):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _fake_issues(monkeypatch, [
        {"number": 40991, "title": "today", "body": "TODAYS BODY",
         "created_at": f"{today}T13:14:24Z"},
    ])
    out = qa.fetch_yesterday()
    assert "TODAYS BODY" not in out
    assert "unchanged" in out  # instructs the agent NOT to claim no-change


# ── The end-to-end fail-soft guarantee, offline ──────────────────────────────
def test_offline_run_exits_zero_and_never_renders_clean(tmp_path):
    """The whole point, executed: run the script with an empty environment and
    an empty PATH (no curl, no npm, no pip, no git, no secrets, no app URL) —
    fully offline, the way a maximally broken runner would.  It must exit 0
    (fail-soft is load-bearing: the agent falls back per check) and the bundle
    it writes must say SKIPPED / unavailable everywhere — never the clean-day
    renders, because "unavailable ≠ clean" is the invariant every probe holds
    (docs/precompute.md)."""
    out_dir = tmp_path / "out"
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    proc = subprocess.run(
        [sys.executable, str(PRECOMPUTE_SCRIPT)],
        env={"PATH": str(empty_bin), "QA_PRECOMPUTE_DIR": str(out_dir)},
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"precompute must exit 0 (fail-soft); got {proc.returncode}\n{proc.stderr}"
    )
    assert "qa_precompute: wrote" in proc.stdout
    bundle = (out_dir / "bundle.md").read_text(encoding="utf-8")
    # Check 9: secret-gated probe self-skips with the reason named.
    assert "SKIPPED: APP_BASE_URL unset" in bundle
    # Check 7: every failed audit renders unavailable, never a clean count.
    assert "pip-audit unavailable" in bundle
    assert "npm audit UNAVAILABLE" in bundle
    assert "Backend majors: unavailable" in bundle
    assert "Frontend majors: unavailable" in bundle
    assert "Frontend HIGH/CRIT (npm audit): none" not in bundle
    # Deploy state: no probe target ⇒ explicitly not determined.
    assert "deploy state NOT determined" in bundle
    assert "undeployed:    0" not in bundle
    # Manifest: every secret honestly unset, values never printed.
    assert "ANTHROPIC_API_KEY=unset" in bundle


def test_bundle_carries_deploy_state_before_yesterday(monkeypatch, tmp_path):
    """main() integration: the run-wide sections land in the bundle in order
    (DEPLOY STATE before YESTERDAY), and the header labels its SHA as the
    checkout — the only SHA in the agent's context must never read as the
    live build."""
    monkeypatch.setattr(qa, "OUT_DIR", tmp_path)
    monkeypatch.setattr(qa, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(qa, "BUNDLE", tmp_path / "bundle.md")
    monkeypatch.setattr(qa, "PRECOMPUTED_CHECKS", ())
    monkeypatch.setattr(qa, "deploy_state",
                        lambda: f"## DEPLOY STATE\n\ndeployed_sha:  {_LIVE}\n")
    monkeypatch.setattr(qa, "fetch_yesterday", lambda: "## YESTERDAY (self-feedback)\n")
    assert qa.main() == 0
    text = (tmp_path / "bundle.md").read_text(encoding="utf-8")
    assert text.index("## DEPLOY STATE") < text.index("## YESTERDAY")
    assert "checkout" in text.splitlines()[0]
    assert "not what is deployed" in text
