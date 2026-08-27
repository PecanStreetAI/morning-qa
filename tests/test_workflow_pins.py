"""Structural + behavioral pins for template/.github/workflows/morning-qa.yml.

This is the suite the README means when it says the docs (and the workflow's
own safety posture) are pinned to the code by tests.  The failure class every
pin here closes is "invariant recorded as prose, not structure": each flag,
pin, and fallback body in the workflow encodes a dated production lesson, and
each lesson has already been lost once by an edit that no test caught.

The three headline groups:

* **Supply-chain pins** — both `npm install -g` lines must carry an EXACT
  version.  These installs run immediately before the agent step, whose env
  holds the Anthropic API key, the read-only database connection string, and
  the app's admin keys; a floating install re-resolves the `latest` dist-tag
  every morning, so a compromised release reaches that secret-bearing
  environment within 24h with no diff to review (flagged in a 2026-07-23
  review of the production instance).  The detector is calibrated BOTH ways —
  every known defeat reds, every equivalent rewrite stays green.
* **Tier-1 CLI enforcement** — `--strict-mcp-config` + `--disallowed-tools`
  are the actual enforcement (`--allowed-tools` is pre-approval, not a
  restriction: a 2026-07-23 production run called Write successfully while it
  was absent from the list).
* **Report classification** — the checked-in qa_severity_label.sh is executed
  against synthetic report bodies (critical / warning-only / in-progress /
  unknown), because it shipped three bugs while it lived as inline workflow
  bash that no test could run (2026-06-03, 2026-07-15, 2026-08-06).
"""

import json
import re

import pytest

from probes import (
    BASH,
    DESIGN_DOC,
    MCP_CONFIG,
    RETRO_WORKFLOW,
    SEV_SCRIPT,
    SKILL,
    TELEMETRY_SCRIPT,
    all_run_steps,
    classify,
    code,
    flat,
    job,
    load_workflow,
    named_step,
    run_label_block,
    shell_lines,
    split_spec,
    step_names,
    unpinned_defect,
    _command_segments,
    _global_install_specs,
    MARKER_VALUE_RE,
)

_LOG_TMP = "/tmp/claude-stdout.log"
_REPORT_TMP = "/tmp/qa-report.md"


def _run_qa():
    return job(load_workflow(), "run-qa")


def _post_issue():
    return job(load_workflow(), "post-issue")


def _agent_lines():
    return shell_lines(named_step(_run_qa(), "Run morning QA skill")["run"])


# ── The two-job split ────────────────────────────────────────────────────────
def test_two_job_split_and_its_guarantees():
    """The split is what makes the daily issue unconditional: run-qa produces
    the report, post-issue (always ubuntu-latest, which ships `gh`) posts it
    with `if: always()` so the issue lands even when run-qa dies.  The 30-min
    cap on run-qa is a deliberate operator decision — a "something is wrong"
    signal, not a budget to quietly raise (a 2026-07-15 production run was
    killed at the then-20-min cap and posted nothing, which is why the
    fallback machinery below exists)."""
    wf = load_workflow()
    assert set(wf["jobs"]) == {"run-qa", "post-issue"}
    rq, pi = _run_qa(), _post_issue()
    assert rq["runs-on"] == "ubuntu-latest"
    assert rq["timeout-minutes"] == 30, (
        "run-qa's 30-minute cap is a deliberate operator decision — a bump "
        "should be a reviewed call, not a drive-by"
    )
    assert pi["runs-on"] == "ubuntu-latest", "post-issue needs `gh` — hosted runner only"
    assert pi["needs"] == "run-qa"
    assert pi["if"] == "always()", (
        "post-issue must run even when run-qa fails — that is the entire "
        "point of the two-job split"
    )
    assert isinstance(pi.get("timeout-minutes"), int) and pi["timeout-minutes"] <= 15
    assert wf["permissions"] == {"contents": "read", "issues": "write"}, (
        "the workflow writes ONE thing (a daily issue); widening permissions "
        "is a posture change, not a tweak"
    )
    assert wf["concurrency"] == {"group": "morning-qa", "cancel-in-progress": False}
    # The artifact download tolerates a vanished run-qa (the synthesize
    # fallback covers that gap).
    dl = named_step(pi, "Download QA report artifact")
    assert dl.get("continue-on-error") is True


# ── Model pin ────────────────────────────────────────────────────────────────
def _pinned_model():
    lines = _agent_lines()
    assert any('claude -p "/morning-qa"' in ln for ln in lines), (
        "claude invocation not found in the 'Run morning QA skill' step"
    )
    for ln in lines:
        if "--model" in ln:
            return ln.strip().split("--model", 1)[1].strip().split()[0].rstrip("\\").strip()
    pytest.fail(
        "claude invocation has NO --model flag.  Unpinned, the CLI default "
        "silently drifted to a larger model class in the production instance "
        "and billed ~30x the design estimate for weeks (2026-07-23).  Pin an "
        "explicit model; never run this workflow on the CLI default."
    )


def test_claude_invocation_pins_explicit_model():
    model = _pinned_model()
    assert model.startswith("claude-"), (
        f"--model value {model!r} is not an explicit claude-* model id "
        "(a shell variable or placeholder defeats the pin)"
    )


def test_model_pin_mirrored_in_design_doc():
    """docs/design.md's cost section quotes the pinned model; re-pinning the
    workflow without updating the doc leaves the operator triaging cost drift
    against a stale name."""
    assert _pinned_model() in DESIGN_DOC.read_text(encoding="utf-8"), (
        "docs/design.md no longer mentions the pinned model — workflow pin "
        "and docs must move in lockstep"
    )


# ── Tier-1 CLI enforcement flags ─────────────────────────────────────────────
def test_invocation_disallows_mutating_file_tools_and_stays_strict():
    """--disallowed-tools removes Write/Edit/NotebookEdit from the agent's
    context, and --strict-mcp-config makes the CI-scoped MCP config
    authoritative (without it --mcp-config is ADDITIVE and a project's own
    .mcp.json — possibly holding write-capable servers — merges in; verified
    live in the production instance, 2026-07).  These two flags ARE the CLI
    half of the Tier-1 read-only posture."""
    lines = _agent_lines()
    line = next((ln for ln in lines if "--disallowed-tools" in ln), None)
    assert line, "claude invocation has NO --disallowed-tools flag"
    m = re.search(r"--disallowed-tools\s+'([^']+)'", line)
    assert m, "--disallowed-tools value is not a single quoted, comma-separated list"
    # Exact comma-delimited tokens, not substrings: a bare `"Edit" in line` is
    # masked by "NotebookEdit", so a value of 'Write,NotebookEdit' (Edit
    # dropped) would falsely pass.
    tokens = {t.strip() for t in m.group(1).split(",")}
    for tool in ("Write", "Edit", "NotebookEdit"):
        assert tool in tokens, f"--disallowed-tools no longer removes {tool} (tokens={tokens})"
    assert "ToolSearch" not in tokens, (
        "ToolSearch must stay available — it loads the deferred read-only MCP verbs"
    )
    assert any("--strict-mcp-config" in ln for ln in lines), (
        "claude invocation has NO --strict-mcp-config; the checkout's own "
        ".mcp.json (which may wire write-capable servers) would be merged in"
    )
    assert any("--mcp-config .mcp.qa.json" in ln for ln in lines)
    # The CI-scoped MCP config itself: read-only flag on, no credential —
    # the URI reaches the subprocess via the workflow env, never this file.
    cfg = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
    servers = cfg["mcpServers"]
    assert set(servers) == {"mongo-ro"}, "only read-only servers belong in .mcp.qa.json"
    assert servers["mongo-ro"]["env"] == {"MDB_MCP_READ_ONLY": "true"}
    assert servers["mongo-ro"]["command"] == "mongodb-mcp-server", (
        "the command must be the pre-installed binary, not an npx cold-fetch "
        "(npx's fetch+install+spawn exceeds the CLI's MCP handshake timeout)"
    )
    assert "mongodb://" not in MCP_CONFIG.read_text(encoding="utf-8")


def test_allowed_tools_is_the_five_readonly_mongo_verbs_and_skill_agrees():
    """The MCP entries in --allowed-tools are exactly the five read-only Mongo
    verbs the Critical cross-check gate needs.  SKILL.md's tool-surface prose
    exists to describe this string, so the two are pinned together — and the
    verbs SKILL.md bans must not creep into the pre-approved list.  (Limit of
    this pin, learned 2026-07-23: --allowed-tools is pre-approval, not
    enforcement — this asserts doc==config, not that the config is a gate.)"""
    line = next((ln for ln in _agent_lines() if "--allowed-tools" in ln), None)
    assert line, "--allowed-tools not found in the agent step"
    m = re.search(r"--allowed-tools\s+'([^']+)'", line)
    assert m, "--allowed-tools value is not a single quoted, comma-separated list"
    allowed = {t.strip() for t in m.group(1).split(",") if t.strip()}

    mcp = {t for t in allowed if t.startswith("mcp__")}
    assert mcp == {
        f"mcp__mongo-ro__{verb}"
        for verb in ("count", "find", "aggregate", "collection-schema", "list-collections")
    }, f"allow-listed MCP surface changed: {sorted(mcp)}"

    skill = SKILL.read_text(encoding="utf-8")
    for tool in ("Read", "Bash", "WebFetch"):
        assert tool in allowed, f"SKILL.md promises {tool}; the workflow no longer lists it"
    for verb in mcp:
        assert verb in skill, f"SKILL.md no longer documents allow-listed verb {verb}"
    # SKILL.md's do-not-call list stays out of the pre-approved surface.
    for banned in ("mongodb-logs", "export", "db-stats", "collection-storage-size",
                   "aggregate-db", "explain", "switch-connection"):
        assert f"mcp__mongo-ro__{banned}" not in allowed, (
            f"{banned} is banned by SKILL.md but pre-approved by the workflow"
        )


# ── Supply-chain: every global npm install is exact-pinned ───────────────────
def test_global_npm_installs_are_exact_pinned():
    """The daily-latest exfiltration channel.  Every job in this workflow
    handles repo secrets, so every run: step is in scope — not just the two
    known install steps."""
    for job_name, step in all_run_steps(load_workflow()):
        defect = unpinned_defect(step["run"])
        assert defect is None, f"{job_name} / {step.get('name', '<unnamed>')!r}: {defect}"


def test_both_known_qa_packages_are_still_installed():
    """Guards the pin above against a false green: it asserts a property of
    whatever installs it FINDS, so deleting an install step would satisfy it
    trivially.  Both packages are load-bearing — the CLI runs the agent, the
    MCP server backs the Mongo cross-check gate."""
    installed = {
        split_spec(spec)[0]
        for _, step in all_run_steps(load_workflow())
        for segment in _command_segments(step["run"])
        for spec in _global_install_specs(segment) or ()
    }
    for pkg in ("@anthropic-ai/claude-code", "mongodb-mcp-server"):
        assert pkg in installed, (
            f"{pkg} is no longer globally installed in morning-qa.yml — intended?  "
            "If the install moved, move this pin with it; do not leave it "
            "passing vacuously."
        )


# Calibration, half 1: every known way to defeat the pin must be caught.
_PINNED = "npm install -g @anthropic-ai/claude-code@2.1.241"


@pytest.mark.parametrize("label,block", [
    ("bare scoped name", "npm install -g @anthropic-ai/claude-code"),
    ("@latest dist-tag", "npm install -g mongodb-mcp-server@latest"),
    ("caret range", "npm install -g mongodb-mcp-server@^1.14.0"),
    ("x-range", "npm install -g mongodb-mcp-server@1.x"),
    ("`npm i` alias", "npm i -g mongodb-mcp-server@latest"),
    ("flag before subcommand", "npm -g install mongodb-mcp-server@latest"),
    ("chained after a pinned install", f"{_PINNED} && npm install -g evil@latest"),
    ("unpinned on a continued line", "npm install -g \\\n  mongodb-mcp-server@latest"),
])
def test_pin_detector_catches_synthetic_defeats(label, block):
    """The pin passes on main by design, so nothing else proves it can fail.
    Without this half, a refactor of the parsing could hollow the guard out
    with CI green — the exact failure mode it exists to close."""
    assert unpinned_defect(block) is not None, f"detector missed the defeat: {label}"


# Calibration, half 2: correct rewrites must NOT red.  A pin that fails on a
# harmless reformat gets deleted by the next person it inconveniences — which
# is how a gate really dies.
@pytest.mark.parametrize("label,block", [
    ("a pinned install line", _PINNED),
    ("line-continuation reformat", "npm install -g \\\n  @anthropic-ai/claude-code@2.1.241"),
    ("prerelease + build metadata", "npm install -g pkg@1.2.3-beta.1+build.5"),
    ("registry flag with a separate value",
     "npm install -g --registry https://registry.npmjs.org/ pkg@1.0.0"),
    ("local (non-global) install is out of scope", "cd frontend && npm ci"),
    ("comment mentioning @latest", "# we used to run npm install -g pkg@latest\nnpm ci"),
])
def test_pin_detector_accepts_equivalent_forms(label, block):
    defect = unpinned_defect(block)
    assert defect is None, f"false failure on {label}: {defect}"


def test_pip_tooling_is_exact_pinned():
    """pip-audit and its transitive msgpack are exact-pinned (`==`) for the
    same unattended-install reasoning as the npm pins — and msgpack's pin is
    additionally a wheel-race guard (a floor re-resolves to latest daily; a
    fresh release whose wheel lags its sdist forces a source build on the
    runner)."""
    run = code(named_step(_run_qa(), "Install Python deps")["run"])
    assert re.search(r"pip install\s+'pip-audit==\d+\.\d+\.\d+'\s+'msgpack==\d+\.\d+\.\d+'", run), (
        "pip-audit / msgpack are no longer exact-pinned on the install line"
    )


# ── Config-surface wiring ────────────────────────────────────────────────────
def test_app_base_url_is_a_repo_variable_on_both_steps():
    """APP_BASE_URL is a repo VARIABLE (not a secret) and must reach BOTH the
    pre-compute step (its cron-health + deploy-receipt probes) and the agent
    step (the checks' own fallback probes).  Missing from either, the affected
    checks silently degrade to SKIPPED — the "unavailable is not clean" class
    surfacing as a permanent coverage loss rather than a red."""
    for step_name in ("Pre-compute deterministic check inputs", "Run morning QA skill"):
        env = named_step(_run_qa(), step_name).get("env") or {}
        assert "APP_BASE_URL" in env, f"{step_name}: APP_BASE_URL missing from env"
        val = str(env["APP_BASE_URL"])
        assert "vars.APP_BASE_URL" in val, f"{step_name}: APP_BASE_URL is not vars-sourced: {val}"
        assert "secrets." not in val, f"{step_name}: APP_BASE_URL must be a variable, not a secret"


def test_precompute_step_wiring_and_secret_posture():
    """The pre-compute step runs BEFORE the agent (it produces the bundle the
    agent reads), fails soft (continue-on-error + its own bounded timeout so a
    hang can never eat the agent's 30-minute budget), and carries a SUBSET of
    the agent's env: never ANTHROPIC_API_KEY, never the Mongo connection
    string (the database stays on the read-only MCP — the agent's judgment
    path)."""
    rq = _run_qa()
    names = step_names(rq)
    pre = "Pre-compute deterministic check inputs"
    assert pre in names, "pre-compute step missing from run-qa"
    assert names.index(pre) < names.index("Run morning QA skill")
    step = named_step(rq, pre)
    assert step.get("continue-on-error") is True
    tm = step.get("timeout-minutes")
    assert isinstance(tm, int) and tm <= 15
    assert ".github/scripts/qa_precompute.py" in step["run"]
    env = step.get("env") or {}
    assert "ANTHROPIC_API_KEY" not in env
    assert "MDB_MCP_CONNECTION_STRING" not in env
    assert "API_ACCESS_KEY" in env  # but DOES carry what its probes need
    agent_env = named_step(rq, "Run morning QA skill").get("env") or {}
    assert "ANTHROPIC_API_KEY" in agent_env and "MDB_MCP_CONNECTION_STRING" in agent_env


def test_tmp_path_lockstep_across_steps():
    """Every path in the report/telemetry chain fails soft by design, so a
    rename at any single site produces no red anywhere — just a permanently
    degraded artifact.  Pin each literal at its producing AND consuming steps,
    including the stale-workspace clean (on a self-hosted runner /tmp persists
    between runs, and yesterday's report or bundle must never post as
    today's)."""
    rq = _run_qa()
    clean = named_step(rq, "Clean stale workspace artifacts")["run"]
    agent = named_step(rq, "Run morning QA skill")["run"]
    telemetry = named_step(rq, "Append run telemetry to report")["run"]
    upload = named_step(rq, "Upload run artifacts")["with"]["path"]

    for path, sites in {
        _LOG_TMP: {"clean": clean, "agent(tee)": agent, "telemetry": telemetry, "upload": upload},
        _REPORT_TMP: {"clean": clean, "agent(report)": agent, "telemetry": telemetry, "upload": upload},
        "/tmp/qa-precompute": {"clean": clean, "upload": upload},
    }.items():
        for site, text in sites.items():
            assert path in text, f"{path} missing from {site} step — cross-step lockstep broken"


def test_telemetry_step_invokes_the_checked_in_script():
    """The stream-json cost parser must stay a checked-in script, never an
    inline `node -e` payload (untestable + bash-single-quote copy-edit
    hazard) — and it must run even when the agent step failed, because a
    partial run's cost is exactly the run worth examining."""
    step = named_step(_run_qa(), "Append run telemetry to report")
    assert step.get("if") == "always()"
    assert any(
        ".github/scripts/qa_run_telemetry.js" in ln and _LOG_TMP in ln and _REPORT_TMP in ln
        for ln in shell_lines(step["run"])
    )
    assert "node -e" not in step["run"]
    assert TELEMETRY_SCRIPT.is_file()


# ── Fallback bodies (the no-report / no-artifact paths) ─────────────────────
def test_run_qa_no_report_fallback_carries_the_unknown_marker():
    """The 'agent died before writing even the skeleton' body once carried NO
    severity marker, so the single hardest failure mode reached the classifier
    marker-less and posted unlabeled (2026-08-06).  `unknown` is the honest
    value: zero checks ran, so coverage is unestablished — not `none` (a
    clean-day claim) and not `critical` (there is no finding)."""
    lines = shell_lines(named_step(_run_qa(), "Run morning QA skill")["run"])
    assert any(re.search(r"qa-max-severity:\s*unknown", ln) for ln in lines), (
        "run-qa's no-report fallback no longer emits the unknown marker — "
        "it would post unlabeled"
    )


def test_post_issue_no_artifact_fallback_classifies_critical_not_incomplete(tmp_path):
    """post-issue's 'no artifact at all' fallback deliberately classifies
    critical-and-NOT-incomplete, unlike run-qa's no-report fallback
    (unknown-and-incomplete).  The asymmetry is intentional — no artifact
    means the job died or the runner vanished (no report, no stdout, no
    telemetry), and `critical` pins the day open through the supersede step —
    but it is exactly the kind of intent that erodes into an accident, so the
    synthesized body is executed through the real classifier."""
    run = named_step(_post_issue(), "Synthesize fallback report if artifact is missing")["run"]
    body = "\n".join(
        re.sub(r'^\s*echo\s+"?(.*?)"?\s*$', r"\1", ln)
        for ln in shell_lines(run) if ln.strip().startswith("echo")
    )
    assert "qa-max-severity" in body, "post-issue fallback lost its severity marker"
    assert classify(tmp_path, body) == ("1", "0"), (
        "post-issue's missing-artifact fallback changed classification — if "
        "intended, update the fallback commentary in the workflow in lockstep"
    )


# ── Labels: created, applied, removed, delegated ─────────────────────────────
def test_labels_created_applied_removed_and_classifier_delegated():
    """All three halves matter.  Create: `gh issue edit --add-label` errors on
    a label that does not exist in-repo (the very first scheduled run failed
    exactly there, 2026-05-20).  Apply: otherwise an aborted run posts
    unlabeled.  Remove: a manual re-run EDITS the same day's issue, and a
    stale label on the downgraded axis is the 2026-07-15 add-only bug (in
    either direction).  And the classification itself must stay delegated to
    the checked-in script — inline marker parsing is where all three of its
    historical bugs lived."""
    pi = _post_issue()
    create = named_step(pi, "Ensure required labels exist")["run"]
    for label in ("qa-agent-daily", "priority:critical", "qa-agent-incomplete"):
        assert re.search(rf"gh label create\s+{re.escape(label)}", create), (
            f"label {label} is never created; gh errors when applying an unknown label"
        )
    lines = shell_lines(named_step(pi, "Post / update daily QA Issue")["run"])
    for frag in ("--label qa-agent-incomplete", "--add-label qa-agent-incomplete",
                 "--remove-label qa-agent-incomplete",
                 "--add-label priority:critical", "--remove-label priority:critical"):
        assert any(frag in ln for ln in lines), f"post-issue never passes `{frag}`"
    assert SEV_SCRIPT.is_file()
    assert any(".github/scripts/qa_severity_label.sh" in ln for ln in lines), (
        "post-issue no longer invokes the classifier script"
    )
    inline = [ln for ln in lines if "qa-max-severity" in ln]
    assert not inline, (
        f"the severity marker is being parsed inline again — that is where "
        f"the classifier's three historical bugs lived: {inline}"
    )


# ── The classifier, executed against synthetic report bodies ─────────────────
# Severity ("what did we find") and completeness ("did we look at all") are
# INDEPENDENT axes; collapsing them is what produced the 2026-08-06 bug (a run
# that completed ZERO checks posted byte-indistinguishably from a clean full
# day).  The bodies below use this template's own N=2 roster shape.

_BODY_ZERO_CHECKS = (
    "<!-- qa-max-severity: none -->\n"
    "# Morning QA — 2026-08-06\n"
    "\n"
    "**Status:** ⏳ In progress (0/2 checks)\n"
    "\n"
    "---\n"
    "_Run telemetry: model `claude-x` · cost $2.01 · 34 turns · 9 min_\n"
)

_BODY_COMPLETE_CLEAN = (
    "<!-- qa-max-severity: none -->\n"
    "# Morning QA — 2026-08-06\n"
    "\n"
    "**Status:** ✅ Complete\n"
    "\n"
    "## Headline\n"
    "\n"
    "All clean — 2 checks, 0 findings.\n"
    "\n"
    "### 🔴 Critical\n"
    "\n"
    "_None today._\n"
)

_BODY_PARTIAL_NONE = (
    "<!-- qa-max-severity: none -->\n"
    "# Morning QA — 2026-08-06\n"
    "\n"
    "**Status:** ⏳ In progress (1/2 checks)\n"
)

_BODY_PARTIAL_CRITICAL = (
    "<!-- qa-max-severity: critical -->\n"
    "# Morning QA — 2026-08-06\n"
    "\n"
    "**Status:** ⏳ In progress (1/2 checks)\n"
    "\n"
    "### 🔴 Critical\n"
    "\n"
    "Check 9 — nightly_ingest stuck 3 days.\n"
)

_BODY_COMPLETE_CRITICAL = (
    "<!-- qa-max-severity: critical -->\n"
    "# Morning QA — 2026-08-06\n"
    "\n"
    "**Status:** ✅ Complete\n"
    "\n"
    "## Headline\n"
    "\n"
    "3 findings (1 Critical, 1 Warning, 1 Info)\n"
)


def test_classifier_flags_a_zero_check_run_incomplete_not_critical(tmp_path):
    """The regression itself: a run that died before completing any check
    posted with NO label and read as a clean day in the operator's email
    (2026-08-06).  An aborted run has no finding, so it must not read as
    Critical either — that would pollute the Critical-false-positive budget
    with infrastructure flake."""
    assert classify(tmp_path, _BODY_ZERO_CHECKS) == ("0", "1")


def test_classifier_keeps_the_two_axes_independent(tmp_path):
    """A complete clean day collects neither flag; a complete day holding a
    Critical is critical-only; a run that found a Critical and THEN died has a
    real finding AND unestablished coverage — both flags at once."""
    assert classify(tmp_path, _BODY_COMPLETE_CLEAN) == ("0", "0")
    assert classify(tmp_path, _BODY_COMPLETE_CRITICAL) == ("1", "0")
    assert classify(tmp_path, _BODY_PARTIAL_CRITICAL) == ("1", "1")
    # Fixing only the skeleton's marker would MOVE the 2026-08-06 bug, not
    # close it: a compliant agent that dies after one clean check writes a
    # genuine `none` plus an In-progress Status — completeness must come from
    # the Status line, independent of the marker.
    assert classify(tmp_path, _BODY_PARTIAL_NONE) == ("0", "1")


def test_classifier_markerless_fallback_and_the_empty_header_trap(tmp_path):
    """A marker-less report (older skill revision / agent slip) falls back to
    a NON-ZERO Critical count in the headline — and EVERY report carries a
    '### 🔴 Critical' header (a clean day prints '_None today._' beneath it),
    so header presence must never imply severity.  Grepping for the header is
    the 2026-06-03 bug that stamped the entire backlog priority:critical."""
    legacy_critical = (
        "# Morning QA — 2026-08-06\n\n**Status:** ✅ Complete\n\n"
        "## Headline\n\n5 findings (3 Critical, 2 Warning)\n"
    )
    assert classify(tmp_path, legacy_critical) == ("1", "0")
    marked = "<!-- qa-max-severity: none -->\n### 🔴 Critical\n\n_None today._\n"
    markerless = "# Morning QA\n\n**Status:** ✅ Complete\n\n### 🔴 Critical\n\n_None today._\n"
    assert classify(tmp_path, marked) == ("0", "0")
    assert classify(tmp_path, markerless) == ("0", "0")


def test_classifier_ignores_marker_text_quoted_in_the_report_body(tmp_path):
    """The marker is the report's FIRST line, so only the header region may be
    consulted.  The body is free-form LLM output that routinely QUOTES these
    strings (a diff-review check printing commit subjects will, the day a
    classifier change lands).  Whole-file greps failed in three directions in
    the production instance; the false-NEGATIVE one — a quoted marker
    satisfying the marker-present gate and suppressing the legacy headline
    fallback on a genuine multi-Critical day — is the dangerous one."""
    quoted = "\n\n### Check 10\n\n- abc1234 — fix: <!-- qa-max-severity: critical --> in a subject\n"
    assert classify(tmp_path, _BODY_COMPLETE_CLEAN + quoted) == ("0", "0")
    markerless = (
        "# Morning QA — 2026-08-07\n\n**Status:** ✅ Complete\n\n"
        "## Headline\n\n5 findings (3 Critical, 2 Warning)\n\n"
        "## Check results\n\n"
        "All checks ran; details below.\n\n"
        "### Check 10 — diff review\n" + quoted
    )
    assert classify(tmp_path, markerless) == ("1", "0"), (
        "a quoted marker in the body suppressed the legacy headline fallback — "
        "a real 3-Critical day would post unlabeled and be auto-closed tomorrow"
    )


def test_classifier_unreadable_report_defaults_to_incomplete(tmp_path):
    """Unavailable is not clean, applied to the classifier itself: never
    assert 'clean' about a report we could not examine.  Defensive — the
    synthesize step normally guarantees a non-empty file — but the default
    must be safe in both the absent and the empty case."""
    for body, label in ((None, "report file absent"), ("", "report file empty")):
        assert classify(tmp_path, body) == ("0", "1"), f"unsafe default for: {label}"


# Calibration, half 1 — every shape an ABORTED run can reach the poster as
# must be flagged.  Same two-halves discipline as the npm pin: a guard whose
# failure paths are never exercised can be hollowed out silently.
@pytest.mark.parametrize("label,body", [
    ("zero-checks in-progress body", _BODY_ZERO_CHECKS),
    ("bare post-fix skeleton", "<!-- qa-max-severity: unknown -->\n# Morning QA\n"),
    ("compliant partial, nothing found yet", _BODY_PARTIAL_NONE),
    ("run-qa's no-report fallback body",
     "<!-- qa-max-severity: unknown -->\n# Morning QA — 2026-08-06\n\n"
     "**Status:** 🔴 Agent did not produce a report.\n"),
    ("Status line without the hourglass emoji",
     "<!-- qa-max-severity: none -->\n**Status:** In progress (1/2 checks)\n"),
    # Reachable, not hypothetical: the skill's step-1 skeleton sits inside a
    # numbered list, so the template the agent copies from is itself indented —
    # a column-0 anchor let an indented in-progress report through as CLEAN
    # (found by adversarial probing, 2026-08-06).
    ("Status line indented (the skill's own template is)",
     "<!-- qa-max-severity: none -->\n   **Status:** ⏳ In progress (0/2 checks)\n"),
    ("Status field without bold markers",
     "<!-- qa-max-severity: none -->\nStatus: ⏳ In progress (1/2 checks)\n"),
    ("CRLF line endings",
     "<!-- qa-max-severity: none -->\r\n**Status:** In progress (0/2)\r\n"),
])
def test_incomplete_detector_catches_every_aborted_shape(tmp_path, label, body):
    _, is_incomplete = classify(tmp_path, body)
    assert is_incomplete == "1", f"aborted run not flagged: {label}"


# Calibration, half 2 — a complete report must NOT be flagged.  A guard that
# fires on healthy days trains the operator to ignore the label — the same
# alarm-fatigue failure the 2026-06-03 bug caused from the other side.
@pytest.mark.parametrize("label,body", [
    ("complete + clean", _BODY_COMPLETE_CLEAN),
    ("complete + critical", _BODY_COMPLETE_CRITICAL),
    ("markerless legacy complete report",
     "# Morning QA\n\n**Status:** ✅ Complete\n\n## Headline\n\nAll clean.\n"),
    ("a FINDING that mentions an in-progress migration",
     "<!-- qa-max-severity: warning -->\n**Status:** ✅ Complete\n\n### 🟡 Warning\n\n"
     "Check 9 — the index swap is in progress (3/5 shards) on the box.\n"),
    ("body quotes a commit subject naming the marker",
     "<!-- qa-max-severity: none -->\n# Morning QA — 2026-08-07\n\n"
     "**Status:** ✅ Complete\n\n## Headline\n\nAll clean — 2 checks.\n\n"
     "## Check results\n\nAll green.\n\n### Check 10 — daily diff review\n\n"
     "- 9f2c1ab — fix(qa): in-progress skeleton emitted qa-max-severity: none; "
     "emit qa-max-severity: unknown + the qa-agent-incomplete label\n"),
])
def test_incomplete_detector_does_not_fire_on_complete_reports(tmp_path, label, body):
    _, is_incomplete = classify(tmp_path, body)
    assert is_incomplete == "0", f"false incomplete on: {label}"


# ── The workflow's label glue, executed ──────────────────────────────────────
def test_label_block_applies_incomplete_to_an_aborted_run(tmp_path):
    create, edit = run_label_block(tmp_path, _BODY_ZERO_CHECKS)
    assert "--label qa-agent-incomplete" in create
    assert "--add-label qa-agent-incomplete" in edit
    assert "priority:critical" not in create, "an aborted run is not a Critical finding"


def test_label_block_leaves_a_clean_day_with_only_the_daily_label(tmp_path):
    """A complete clean run collects NEITHER priority label and actively
    clears both — a late re-run edits the same day's issue, and a stale label
    in either direction is the 2026-07-15 add-only-label bug."""
    create, edit = run_label_block(tmp_path, _BODY_COMPLETE_CLEAN)
    assert create.strip() == "--label qa-agent-daily"
    assert "--remove-label priority:critical" in edit
    assert "--remove-label qa-agent-incomplete" in edit
    assert "--add-label qa-agent-incomplete" not in edit


def test_label_block_fails_in_opposite_directions_when_classifier_is_unreachable(tmp_path):
    """The residual of the whole class, pinned.  A missing/broken classifier
    parses empty, and the two flags must fail OPPOSITE ways: is_critical fails
    CLOSED (a classifier crash must not manufacture a daily false Critical —
    the 2026-06-03 alarm-fatigue shape) while is_incomplete fails OPEN (a
    broken classifier is precisely a state in which the day was NOT
    established clean).  It must also not REMOVE priority:critical — that
    would disarm the alarm on the one path where we are already blind."""
    create, edit = run_label_block(tmp_path, _BODY_COMPLETE_CRITICAL, classifier=False)
    assert "priority:critical" not in create, "must not manufacture a Critical when blind"
    assert "priority:critical" not in edit, (
        "a blind classifier touched priority:critical — a genuinely critical "
        "day could be silently disarmed and auto-closed by supersede"
    )
    assert "--label qa-agent-incomplete" in create, (
        "with the classifier unreachable the day was labeled CLEAN — a "
        "degraded classifier must never produce a benign-looking report"
    )
    assert "--add-label qa-agent-incomplete" in edit


# ── SKILL.md ↔ classifier lockstep ──────────────────────────────────────────
def test_skill_skeleton_declares_unknown_and_classifies_incomplete(tmp_path):
    """The root cause of the 2026-08-06 defect was the skill SPECIFYING `none`
    on the in-progress skeleton.  Two halves: the skeleton's marker must stay
    `unknown`, and — because the Status line is the second machine-read field
    and a reworded skeleton would keep the vocabulary pin green — the literal
    skeleton the skill tells the agent to write is fed through the real
    classifier, indentation and all."""
    skill = SKILL.read_text(encoding="utf-8")
    parts = skill.split("**Before Check 0**", 1)
    assert len(parts) == 2, "SKILL.md Output-protocol step 1 not found — restructured?"
    blocks = re.findall(r"```\n(.*?)```", parts[1], re.S)
    assert blocks, "SKILL.md step-1 skeleton fenced block not found"
    skeleton = blocks[0]
    assert "qa-max-severity: unknown" in skeleton, (
        "the in-progress skeleton no longer declares `unknown`; back at "
        "`none`, a zero-check run posts as a clean day again"
    )
    assert "qa-max-severity: none" not in skeleton
    assert "Status:" in skeleton, f"step-1 skeleton has no Status field:\n{skeleton}"
    assert classify(tmp_path, skeleton) == ("0", "1"), (
        "the skeleton SKILL.md tells the agent to write does not classify as "
        "incomplete — the 2026-08-06 defect, reopened by a reword"
    )


def test_skill_marker_vocabulary_matches_the_classifier():
    """Drift here fails SILENTLY and in the dangerous direction: a value
    documented in SKILL.md but absent from the script's alternation is treated
    as 'marker missing' and falls through to the legacy headline path."""
    alternation = re.search(
        r"qa-max-severity:\[\[:space:\]\]\*\(([a-z|]+)\)",
        SEV_SCRIPT.read_text(encoding="utf-8"),
    )
    assert alternation, "classifier's marker alternation not found — rewritten?"
    script_vocab = set(alternation.group(1).split("|"))
    skill_vocab = set(MARKER_VALUE_RE.findall(SKILL.read_text(encoding="utf-8")))
    assert skill_vocab == script_vocab, (
        f"SKILL.md documents {sorted(skill_vocab)} but the classifier "
        f"recognizes {sorted(script_vocab)} — a value in one and not the "
        "other is silently un-recognized at post time"
    )
    assert "unknown" in script_vocab and "none" in script_vocab


# ── The weekly retro workflow (light pins) ───────────────────────────────────
def test_retro_workflow_uses_exact_title_match_and_the_checked_in_parser():
    """Two dated lessons: `gh issue list --search` does TOKENIZED matching and
    fuzzy-matched yesterday's issue in the production instance (2026-06-06 —
    today's report edited into a wrong-day issue that supersede then closed),
    so the reuse lookup must jq-filter on EXACT title equality; and the parser
    stays a checked-in, unit-tested script rather than inline bash."""
    doc = load_workflow(RETRO_WORKFLOW)
    retro = job(doc, "retro")
    runs = "\n".join(code(s["run"]) for s in retro["steps"] if s.get("run"))
    assert "python .github/scripts/qa_ledger_retro.py" in runs
    assert "select(.title ==" in runs, "sign-off issue lookup no longer exact-matches the title"
    assert "--search" not in runs, (
        "`gh issue list --search` is tokenized matching — the 2026-06-06 "
        "wrong-issue class; keep the --jq exact-title filter"
    )
    assert doc["permissions"] == {"contents": "read", "issues": "write"}
